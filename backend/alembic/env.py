"""Alembic environment: the backend's models are the target metadata, the
database is the one the backend reads from ``POSTGRES_*`` (or
``ALEMBIC_DATABASE_URL``, for a SQLite file in a test or a CI check)."""

from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import engine_from_config, pool

from alembic import context

_BACKEND = Path(__file__).resolve().parent.parent
for entry in (_BACKEND, _BACKEND / "app"):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

import models  # noqa: F401 - registers every table on SQLModel.metadata
from migrations import database_url, include_object
from sqlmodel import SQLModel

config = context.config
if config.config_file_name is not None and os.getenv("ALEMBIC_QUIET") != "1":
    # Keep the loggers that already exist: migrations also run inside the
    # backend's process (migrations.upgrade_to_head), where the default
    # disable_existing_loggers=True would silence every logger made before.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = SQLModel.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=include_object,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = config.attributes.get("connection")
    if connectable is None:
        section = dict(config.get_section(config.config_ini_section) or {})
        section["sqlalchemy.url"] = database_url()
        connectable = engine_from_config(
            section, prefix="sqlalchemy.", poolclass=pool.NullPool
        )
        with connectable.connect() as connection:
            _run(connection)
    else:
        _run(connectable)


def _run(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_object=include_object,
        render_as_batch=connection.dialect.name == "sqlite",
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
