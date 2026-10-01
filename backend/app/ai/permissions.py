"""The AI module's permissions, added to the platform catalogue at mount time.

`app.core.permissions` is not edited: with the module off these permissions do
not exist anywhere. `register()` is idempotent and runs from `mount()` and from
the AI seed step (which also writes them to the permissions tables).
"""

from __future__ import annotations

from app.core import permissions as core

AI_PERMISSIONS: dict[str, tuple[str, str]] = {
    "AI_VIEW": ("ai", "See AI insights, market data and briefs"),
    "AI_CHAT": ("ai", "Ask the AI assistant questions about the book"),
    "AI_ADMIN": ("ai", "Manage AI providers, the master switch and data policy"),
    "AI_AUDIT": ("ai", "Read the AI egress log: exactly what was sent to providers"),
    "AI_MARKET_EDIT": ("ai", "Enter Bangladesh Bank market rates (treasury desk)"),
}

#: role -> AI permissions it gains.
AI_GRANTS: dict[str, set[str]] = {
    "ADMIN": {"AI_VIEW", "AI_CHAT", "AI_ADMIN", "AI_AUDIT", "AI_MARKET_EDIT"},
    "FTP_MANAGER": {"AI_VIEW", "AI_CHAT", "AI_MARKET_EDIT"},
    "FTP_APPROVER": {"AI_VIEW", "AI_CHAT"},
    "ANALYST": {"AI_VIEW", "AI_CHAT"},
    "DATA_OPERATOR": {"AI_VIEW"},
    # Branch managers ask about their own branch: every query runs in the
    # asker's scope, so a branch user cannot reach another branch's figures.
    "VIEWER": {"AI_VIEW", "AI_CHAT"},
    "AUDITOR": {"AI_VIEW", "AI_AUDIT"},
}


def register() -> None:
    core.PERMISSIONS.update(AI_PERMISSIONS)
    for role, extra in AI_GRANTS.items():
        spec = core.ROLES.get(role)
        if spec is not None:
            # A new set, never an in-place update: several roles were built
            # from the same base set, and mutating it would leak grants.
            spec["permissions"] = set(spec["permissions"]) | extra
