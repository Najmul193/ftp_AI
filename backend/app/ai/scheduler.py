"""In-process scheduler for the AI module's jobs.

One background thread. Every job re-checks the master switch and takes an
advisory lock (see `market.service.run_job`), so it is safe with several API
workers and does nothing while AI is off.

A host that sleeps when idle (a free Render web service) stops this thread
too; an external cron calling `POST /ai/jobs/tick` wakes the host and runs
whatever is due, so nothing depends on the thread alone.
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler

from app.ai.config import ai_settings
from app.ai.market import service

log = logging.getLogger(__name__)
_sched: BackgroundScheduler | None = None


def start() -> None:
    global _sched
    if _sched is not None or not ai_settings().AI_SCHEDULER_ENABLED:
        return
    # Only in a served process. `app.cli.warm_cache` runs the app under a
    # TestClient after every upload, which fires startup handlers too; it
    # must not start collecting market data.
    if not any(m in sys.modules for m in ("uvicorn", "gunicorn", "hypercorn")):
        log.info("AI scheduler not started: not running under an ASGI server")
        return
    s = BackgroundScheduler(timezone="Asia/Dhaka", daemon=True,
                            job_defaults={"coalesce": True, "max_instances": 1,
                                          "misfire_grace_time": 300})
    # Staggered first runs: not all at once, and not during start-up.
    first = datetime.now().astimezone() + timedelta(seconds=45)
    for i, (job, minutes) in enumerate(service.JOBS.items()):
        s.add_job(service.run_job, "interval", minutes=minutes, args=[job],
                  id=f"ai:{job}", next_run_time=first + timedelta(seconds=20 * i))
    s.start()
    _sched = s
    log.info("AI scheduler started: %s", ", ".join(service.JOBS))


def stop() -> None:
    global _sched
    if _sched is not None:
        _sched.shutdown(wait=False)
        _sched = None
