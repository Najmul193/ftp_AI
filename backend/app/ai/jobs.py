"""The AI module's scheduled jobs: what runs, how often, and the run record.

Every run re-checks the master switch, takes a transaction-level advisory lock
(so two processes never run the same job at once) and is recorded in
`ai_job_runs` -- except a run that found nothing to do, which is dropped to
keep the record readable.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Callable

from sqlalchemy import desc, func, select, text
from sqlalchemy.orm import Session

from app.ai import settings_service
from app.ai.insights import engine
from app.ai.market import service as market
from app.ai.public import service as public
from app.ai.forecast import track
from app.ai.models import JobRun
from app.core.db import session_scope

log = logging.getLogger(__name__)

#: job -> minimum minutes between runs. `insights` is cheap when nothing has
#: changed (it compares a fingerprint first), so it can run often.
JOBS = {"market_news": 15, "market_prices": 180, "market_bb": 120, "insights": 5,
        "brief": 30, "prune": 24 * 60, "public_data": 12 * 60, "forecasts": 6 * 60}

_RUNNERS: dict[str, Callable[..., dict]] = {"market_news": market.collect_news, "market_prices": market.collect_prices,
            "market_bb": market.collect_bb, "insights": engine.refresh,
            "brief": engine.scheduled_brief, "prune": market.prune,
            "public_data": public.collect, "forecasts": track.run}
_LOCK_BASE = 820_270_000


def run_job(job: str, trigger: str = "schedule", **kwargs) -> dict:
    """Run one job now, if AI is on and no other process is running it."""
    with session_scope() as db:
        if not settings_service.state(db).enabled:
            return {"job": job, "status": "skipped", "reason": "AI is switched off"}
        # A restarted server starts a fresh scheduler whose first runs come
        # within a minute; without this every restart re-read every source.
        if trigger == "schedule" and job not in due_jobs(db):
            return {"job": job, "status": "skipped", "reason": "ran recently"}
        key = _LOCK_BASE + list(_RUNNERS).index(job)
        if not db.scalar(text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": key}):
            return {"job": job, "status": "skipped", "reason": "already running elsewhere"}
        run = JobRun(job=job, trigger=trigger, status="running")
        db.add(run)
        db.flush()
        try:
            if job in ("market_bb", "public_data") and trigger == "manual":
                kwargs.setdefault("manual", True)
            detail = _RUNNERS[job](db, **kwargs)
            # Dates and Decimals in a job's report stored as text: the run
            # record is JSON, and a report must never fail its own job.
            detail = json.loads(json.dumps(detail, default=str))
            run.status = ("blocked" if detail.get("blocked")
                          else "skipped" if detail.get("skipped")
                          else "partial" if detail.get("errors") else "ok")
            run.detail = detail
        except Exception as exc:  # noqa: BLE001 - a job failure is recorded, never raised
            log.exception("AI job %s failed", job)
            db.rollback()
            with session_scope() as s2:
                s2.add(JobRun(job=job, trigger=trigger, status="failed",
                              finished_at=datetime.now(timezone.utc), detail={"error": str(exc)}))
            return {"job": job, "status": "failed", "error": str(exc)}
        if run.status == "skipped":
            db.delete(run)
            return {"job": job, "status": "skipped", "reason": detail.get("skipped")}
        run.finished_at = datetime.now(timezone.utc)
        return {"job": job, "status": run.status, **detail}


def due_jobs(db: Session) -> list[str]:
    """Jobs whose last successful run is older than their interval."""
    out = []
    now = datetime.now(timezone.utc)
    for job, minutes in JOBS.items():
        last = db.scalar(select(func.max(JobRun.started_at))
                         .where(JobRun.job == job, JobRun.status.in_(("ok", "partial"))))
        if last is None or now - last >= timedelta(minutes=minutes):
            out.append(job)
    return out


def status(db: Session) -> dict:
    out = {}
    for job in JOBS:
        last = db.scalar(select(JobRun).where(JobRun.job == job).order_by(desc(JobRun.id)).limit(1))
        ok = db.scalar(select(func.max(JobRun.finished_at))
                       .where(JobRun.job == job, JobRun.status.in_(("ok", "partial"))))
        detail = (last.detail or {}) if last else {}
        out[job] = {"last_status": last.status if last else None,
                    "last_run": last.started_at if last else None,
                    "last_success": ok,
                    "errors": detail.get("errors") or ([detail["error"]] if detail.get("error") else None),
                    "blocked": detail.get("blocked"),
                    "pages": detail.get("pages")}
    return out
