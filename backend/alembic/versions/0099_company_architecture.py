"""The architecture becomes the company's, versioned (issue #329, finding F3
of docs/retrospectives/2026-11-self-hosting.md).

- ``specifications.architecture_id``: the version of the company's
  architecture a specification was planned against (no foreign key: an
  architecture names its specification too).
- The existing architectures become the versions of their company's
  architecture: numbered 1, 2, 3… per company in the order they were approved
  (never-approved drafts after them, by id); the newest approved one stays
  approved and the other approved ones become superseded, so each company has
  one current architecture; each approved version supersedes the one approved
  before it. Every row keeps its ``specification_id``, and a specification
  names the newest approved version drafted for it. Module JSON is left as it
  is: a version without ``earlier_covers`` covers its own specification only,
  and the reads fall back to the newest version that covers a specification,
  so a sprint of an older specification keeps its architecture.

The downgrade drops the column and numbers the versions per specification
again (by id), approving again the newest approved version of a
specification left without one; which superseded rows were approved before
the upgrade is not recorded, so that is the closest state it can restore.

Follows 0036 (open pull request #582) at merge; renumbered then.

Revision ID: 0099
Revises: 0035
Create Date: 2026-11-15 23:00:00 UTC
"""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa

from alembic import op

revision = "0099"
down_revision = "0035"
branch_labels = None
depends_on = None

ARCHITECTURES = sa.table(
    "architectures",
    sa.column("id", sa.Integer()),
    sa.column("company_id", sa.Integer()),
    sa.column("specification_id", sa.Integer()),
    sa.column("status", sa.String()),
    sa.column("version", sa.Integer()),
    sa.column("supersedes_id", sa.Integer()),
    sa.column("approved_at", sa.DateTime()),
)
SPECIFICATIONS = sa.table(
    "specifications",
    sa.column("id", sa.Integer()),
    sa.column("architecture_id", sa.Integer()),
)


def _rows(connection) -> list:
    return list(
        connection.execute(
            sa.select(
                ARCHITECTURES.c.id,
                ARCHITECTURES.c.company_id,
                ARCHITECTURES.c.specification_id,
                ARCHITECTURES.c.status,
                ARCHITECTURES.c.supersedes_id,
                ARCHITECTURES.c.approved_at,
            )
        )
    )


def _approval_order(row) -> tuple:
    approved = row.approved_at
    return (approved is None, approved or datetime.min, row.id)


def _update(connection, arch_id: int, **values) -> None:
    connection.execute(
        ARCHITECTURES.update().where(ARCHITECTURES.c.id == arch_id).values(**values)
    )


def upgrade() -> None:
    with op.batch_alter_table("specifications", schema=None) as batch:
        batch.add_column(sa.Column("architecture_id", sa.Integer(), nullable=True))
    op.create_index(
        "ix_specifications_architecture_id", "specifications", ["architecture_id"]
    )

    connection = op.get_bind()
    by_company: dict[int, list] = {}
    for row in _rows(connection):
        by_company.setdefault(row.company_id, []).append(row)
    planned_against: dict[int, int] = {}
    for rows in by_company.values():
        ordered = sorted(rows, key=_approval_order)
        approved = [r for r in ordered if r.status == "approved"]
        current = approved[-1].id if approved else None
        previous = None
        for number, row in enumerate(ordered, 1):
            values: dict = {"version": number}
            if row.status == "approved" and row.id != current:
                values["status"] = "superseded"
            if row.approved_at is not None:
                values["supersedes_id"] = previous
                previous = row.id
                planned_against[row.specification_id] = row.id
            _update(connection, row.id, **values)
    for spec_id, arch_id in planned_against.items():
        connection.execute(
            SPECIFICATIONS.update()
            .where(SPECIFICATIONS.c.id == spec_id)
            .values(architecture_id=arch_id)
        )


def downgrade() -> None:
    connection = op.get_bind()
    by_spec: dict[int, list] = {}
    for row in _rows(connection):
        by_spec.setdefault(row.specification_id, []).append(row)
    for rows in by_spec.values():
        ordered = sorted(rows, key=lambda r: r.id)
        for number, row in enumerate(ordered, 1):
            _update(connection, row.id, version=number)
        if not any(r.status == "approved" for r in ordered):
            was = [r for r in ordered if r.approved_at is not None]
            if was:
                _update(connection, max(was, key=_approval_order).id, status="approved")
    op.drop_index("ix_specifications_architecture_id", table_name="specifications")
    with op.batch_alter_table("specifications", schema=None) as batch:
        batch.drop_column("architecture_id")
