"""Apply the AI module's migrations: `python -m app.ai.cli.migrate [target]`.

Built on the Alembic API rather than a second alembic.ini, so there is one
entry point and no chance of pointing the platform's `alembic` command at the
AI history or the reverse.
"""

from __future__ import annotations

import sys
from pathlib import Path

from alembic import command
from alembic.config import Config

HERE = Path(__file__).resolve().parent.parent / "migrations"


def config() -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(HERE))
    cfg.set_main_option("prepend_sys_path", ".")
    return cfg


def main(argv: list[str]) -> int:
    target = argv[1] if len(argv) > 1 else "head"
    if target.startswith("-"):
        command.downgrade(config(), target)
    else:
        command.upgrade(config(), target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
