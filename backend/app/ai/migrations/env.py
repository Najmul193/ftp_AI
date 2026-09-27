"""Alembic environment for the AI module's own migration history.

Separate from the platform's `alembic/` on purpose: its own version table
(`alembic_version_ai`), its own metadata, and it only ever looks at `ai_*`
tables. Running it cannot touch a platform table, and the platform's chain
never learns these tables exist. Invoked by `python -m app.ai.cli.migrate`.
"""

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.ai.models import AiBase
from app.core.config import settings

config = context.config
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)
target_metadata = AiBase.metadata
VERSION_TABLE = "alembic_version_ai"


def _only_ai(obj, name, type_, reflected, compare_to) -> bool:
    """Autogenerate compares AI tables only; platform tables are not ours."""
    if type_ == "table":
        return bool(name) and name.startswith("ai_")
    return True


def run_migrations_offline() -> None:
    context.configure(url=settings.DATABASE_URL, target_metadata=target_metadata,
                      literal_binds=True, compare_type=True,
                      version_table=VERSION_TABLE, include_object=_only_ai)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(config.get_section(config.config_ini_section, {}),
                                     prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata,
                          compare_type=True, version_table=VERSION_TABLE,
                          include_object=_only_ai)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
