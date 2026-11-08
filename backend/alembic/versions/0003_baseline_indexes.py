"""The baseline's indexes on tables it found already there.

``0001`` creates a missing table with its indexes, but on a table that
already existed (one ``db/init-db/init.sql`` created on the first container
start) it added only the indexes the old startup used to add by hand. The
rest of the models' indexes at that revision (``ix_*_company_id`` and the
like) were left missing there. This revision creates each of them a table
lacks; on a database the migrations built, it changes nothing.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-07 20:45:00 UTC
"""

from __future__ import annotations

import importlib.util
from collections.abc import Sequence
from pathlib import Path

import sqlalchemy as sa

from alembic import op

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _baseline_indexes() -> list[tuple[str, str, list[str], bool]]:
    """The index list ``0001`` froze (its ``_schema``), read from that file."""
    path = Path(__file__).with_name("0001_baseline.py")
    spec = importlib.util.spec_from_file_location("baseline_0001", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module._schema()[1]


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    existing: dict[str, set[str]] = {}
    for name, table_name, columns, unique in _baseline_indexes():
        if table_name not in tables:
            continue
        if table_name not in existing:
            existing[table_name] = {
                i["name"] for i in inspector.get_indexes(table_name)
            }
        if name not in existing[table_name]:
            op.create_index(name, table_name, columns, unique=unique)
            existing[table_name].add(name)


def downgrade() -> None:
    # Nothing to undo safely: an index this revision added cannot be told
    # apart from one 0001 created, and dropping either changes no data.
    pass
