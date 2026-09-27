"""Seed the AI module: `python -m app.ai.cli.seed_ai`. Idempotent.

Writes the AI permissions and grants to the permission tables (so role
screens and the audit trail see them) and creates the settings row, switched
off. Run after `python -m app.ai.cli.migrate`.
"""

from __future__ import annotations

from sqlalchemy import select

from app.ai import settings_service
from app.ai.market.service import sync_catalog
from app.ai.permissions import AI_GRANTS, AI_PERMISSIONS, register
from app.core.db import session_scope
from app.models import Permission, Role, RolePermission


def seed() -> None:
    register()
    with session_scope() as s:
        perms: dict[str, Permission] = {}
        for code, (module, desc) in AI_PERMISSIONS.items():
            p = s.scalar(select(Permission).filter_by(code=code))
            if p is None:
                p = Permission(code=code, module=module, description=desc)
                s.add(p)
                s.flush()
            perms[code] = p
        for role_code, codes in AI_GRANTS.items():
            role = s.scalar(select(Role).filter_by(code=role_code))
            if role is None:
                continue
            have = {rp.permission_id for rp in
                    s.scalars(select(RolePermission).filter_by(role_id=role.id))}
            for c in codes:
                if perms[c].id not in have:
                    s.add(RolePermission(role_id=role.id, permission_id=perms[c].id))
        settings_service.ensure_row(s)
        sync_catalog(s)


if __name__ == "__main__":
    seed()
    print("AI module seeded")
