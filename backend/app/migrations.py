"""The schema's version: Alembic (``backend/alembic/``) owns it.

The backend starts only on a database at the migrations' head
(``require_schema_at_head``): the container's entrypoint and the e2e run
``alembic upgrade head`` first, and a developer running uvicorn by hand runs
it too (or sets ``DB_AUTO_UPGRADE=true`` for a local database). A database
behind head is named with both versions in the error, never patched in
place: the ``ALTER TABLE`` statements the old startup applied live in the
first migration now, where they can be read, reviewed and rolled back.

``vector_memory`` is created by ``db/init-db/init.sql`` (it needs pgvector)
and is not a model, so the comparison between the models and the database
leaves it out (``include_object``).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

BACKEND_DIR = Path(__file__).resolve().parent.parent
ALEMBIC_INI = BACKEND_DIR / "alembic.ini"
# Tables the models do not define: created by init.sql, left alone here.
UNMANAGED_TABLES = frozenset({"vector_memory", "performance_metrics", "factory_state"})
UNVERSIONED = "unversioned"


class SchemaBehindError(RuntimeError):
    """The database is not at the migrations' head."""


def database_url() -> str:
    """The URL the migrations run against: an explicit override, else the
    backend's own (``POSTGRES_*``)."""
    override = os.getenv("ALEMBIC_DATABASE_URL")
    if override:
        return override
    from database import _build_database_url

    return _build_database_url()


def include_object(
    obj: Any, name: str | None, type_: str, reflected: bool, compare_to: Any
) -> bool:
    """What the comparison between the models and the database covers."""
    if type_ == "table":
        return name not in UNMANAGED_TABLES
    table = getattr(obj, "table", None)
    return getattr(table, "name", None) not in UNMANAGED_TABLES


def alembic_config(url: str | None = None):
    """The Alembic configuration, with the URL it should use."""
    from alembic.config import Config

    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    if url:
        os.environ["ALEMBIC_DATABASE_URL"] = url
    return config


def head_revision() -> str:
    """The migrations' head, from the scripts on disk."""
    from alembic.script import ScriptDirectory

    heads = ScriptDirectory.from_config(alembic_config()).get_heads()
    if len(heads) != 1:
        raise RuntimeError(f"the migrations have {len(heads)} heads: {heads}")
    return heads[0]


def current_revision(engine: Engine) -> str | None:
    """The version stamped on the database; ``None`` when it has none."""
    with engine.connect() as connection:
        if not inspect(connection).has_table("alembic_version"):
            return None
        row = connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).first()
        return row[0] if row else None


def require_schema_at_head(engine: Engine) -> str:
    """Refuse a database that is not at head, naming both versions."""
    head = head_revision()
    current = current_revision(engine)
    if current == head:
        return head
    raise SchemaBehindError(
        f"the database schema is at {current or UNVERSIONED} and the code "
        f"needs {head}; run `cd backend && alembic upgrade head` (the "
        "backend container's entrypoint does), or set DB_AUTO_UPGRADE=true "
        "for a local database"
    )


def upgrade_to_head(engine: Engine) -> str:
    """Run the migrations up to head on *engine*'s database."""
    from alembic import command

    config = alembic_config()
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    return head_revision()


def schema_differences(engine: Engine) -> list[Any]:
    """What ``alembic check`` would report: the differences between the
    models and *engine*'s database; empty when they agree."""
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from sqlmodel import SQLModel

    with engine.connect() as connection:
        context = MigrationContext.configure(
            connection,
            opts={"include_object": include_object, "compare_type": True},
        )
        return compare_metadata(context, SQLModel.metadata)
