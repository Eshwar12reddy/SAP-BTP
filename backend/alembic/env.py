"""Alembic environment wired to the application's settings and model metadata.

Migrations run over a synchronous psycopg connection; the URL object is passed straight
to SQLAlchemy so credentials containing special characters need no extra escaping.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

import app.models  # noqa: F401  (registers every model on Base.metadata)
from app.core.config import get_settings
from app.db.base import Base

config = context.config
if config.config_file_name is not None:
    # Keep loggers created before migrations ran (e.g. the app's, when run in-process).
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _database_url():
    # Tests pass an explicit URL object via config.attributes to target their own database.
    return config.attributes.get("database_url") or get_settings().database_url


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(
        _database_url(),
        poolclass=pool.NullPool,
        connect_args={"connect_timeout": get_settings().DATABASE_CONNECT_TIMEOUT},
    )
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            # Each migration in its own transaction: a failure leaves earlier ones applied.
            transaction_per_migration=True,
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
