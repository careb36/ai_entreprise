"""DNS verification of a company's SSO domains (issue #512; ``docs/design/SSO.md``):
``sso_domains`` (one row per company and allowed domain: the token the
backend issued for the company to publish as a TXT record, and the status
the last definite DNS answer left) and ``sso_domain_checks`` (each lookup,
append-only: when, why, the outcome and the TXT strings seen).

Backfill: every domain already in ``sso_connections.allowed_domains`` gets a
row marked ``legacy``, unverified, with a fresh random token. A legacy domain
keeps creating and linking accounts during the grace period
(``SSO_LEGACY_DOMAIN_GRACE``, on by default, for
``SSO_LEGACY_DOMAIN_GRACE_DAYS`` from this upgrade), so an upgrade locks
nobody out; the Owner verifies it in the meantime.

Follows 0048 (an API token's creator id).

Revision ID: 0049
Revises: 0048
Create Date: 2026-11-18 18:00:00 UTC
"""

from __future__ import annotations

import json
import secrets
from datetime import UTC, datetime

import sqlalchemy as sa
import sqlmodel

from alembic import op

revision = "0049"
down_revision = "0048"
branch_labels = None
depends_on = None


def _domains(value) -> list[str]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return []
    if not isinstance(value, list):
        return []
    seen: list[str] = []
    for item in value:
        domain = str(item).strip().lower()
        if domain and domain not in seen:
            seen.append(domain)
    return seen


def upgrade() -> None:
    text = sqlmodel.sql.sqltypes.AutoString
    domains = op.create_table(
        "sso_domains",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column(
            "company_id", sa.Integer(), sa.ForeignKey("companies.id"), nullable=False
        ),
        sa.Column("domain", text(), nullable=False),
        sa.Column("token", text(), nullable=False),
        sa.Column("legacy", sa.Boolean(), nullable=False),
        sa.Column("status", text(), nullable=False),
        sa.Column("verified_at", sa.DateTime(), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(), nullable=True),
        sa.Column("last_outcome", text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("company_id", "domain"),
    )
    op.create_index("ix_sso_domains_company_id", "sso_domains", ["company_id"])
    op.create_index("ix_sso_domains_domain", "sso_domains", ["domain"])

    op.create_table(
        "sso_domain_checks",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column(
            "company_id", sa.Integer(), sa.ForeignKey("companies.id"), nullable=False
        ),
        sa.Column("domain", text(), nullable=False),
        sa.Column("checked_at", sa.DateTime(), nullable=False),
        sa.Column("trigger", text(), nullable=False),
        sa.Column("requested_by", text(), nullable=True),
        sa.Column("outcome", text(), nullable=False),
        sa.Column("answer", sa.JSON(), nullable=True),
        sa.Column("detail", text(), nullable=True),
    )
    op.create_index(
        "ix_sso_domain_checks_company_id", "sso_domain_checks", ["company_id"]
    )
    op.create_index("ix_sso_domain_checks_domain", "sso_domain_checks", ["domain"])

    bind = op.get_bind()
    connections = sa.table(
        "sso_connections",
        sa.column("company_id", sa.Integer),
        sa.column("allowed_domains", sa.JSON),
    )
    now = datetime.now(UTC).replace(tzinfo=None)
    rows = [
        {
            "company_id": company_id,
            "domain": domain,
            "token": secrets.token_urlsafe(32),
            "legacy": True,
            "status": "unverified",
            "created_at": now,
        }
        for company_id, allowed in bind.execute(
            sa.select(connections.c.company_id, connections.c.allowed_domains)
        )
        for domain in _domains(allowed)
    ]
    if rows:
        op.bulk_insert(domains, rows)


def downgrade() -> None:
    op.drop_index("ix_sso_domain_checks_domain", table_name="sso_domain_checks")
    op.drop_index("ix_sso_domain_checks_company_id", table_name="sso_domain_checks")
    op.drop_table("sso_domain_checks")
    op.drop_index("ix_sso_domains_domain", table_name="sso_domains")
    op.drop_index("ix_sso_domains_company_id", table_name="sso_domains")
    op.drop_table("sso_domains")
