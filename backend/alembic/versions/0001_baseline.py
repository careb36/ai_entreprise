"""Baseline: the schema as the models defined it at this revision, converging
whatever a database already has.

The schema is frozen here (``_schema``), never read from the live models: a
migration is a snapshot of its revision, so a later model change and its own
migration never leak into this one.

Before this migration the backend created its tables with
``SQLModel.metadata.create_all`` at startup and patched older databases with
``ALTER TABLE … ADD COLUMN IF NOT EXISTS`` statements by hand. A database may
therefore arrive here in three states: empty (the e2e, a fresh install), with
the tables ``db/init-db/init.sql`` created on the first container start, or
with the tables and patches of an older backend. This migration takes each
to the same place: a table the schema below defines and the database lacks
is created whole, with its indexes; a column a table lacks is added (nullable, as the old patches
did); the nullability and default changes, the indexes and the backfills the
old startup applied are applied once more, idempotently, on Postgres.

Revision ID: 0001
Revises:
Create Date: 2026-11-07 19:30:00 UTC
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

# (table, column): made nullable and stripped of its default, as the old
# startup did for columns the models no longer require.
_MADE_OPTIONAL = (
    ("sprints", "duration_days"),
    ("companies", "default_sprint_length_days"),
    ("bug_reports", "category"),
    ("bug_reports", "severity"),
)
# Indexes the old startup created by hand on tables init.sql may have made.
_INDEXES = (
    (
        "ix_assistant_runtime_state_company_id",
        "assistant_runtime_state",
        ["company_id"],
        False,
    ),
    (
        "ix_assistant_runtime_state_assistant_key",
        "assistant_runtime_state",
        ["assistant_key"],
        False,
    ),
    ("ix_review_comments_external_id", "review_comments", ["external_id"], False),
    ("ix_audit_log_company_id", "audit_log", ["company_id"], False),
    ("ux_audit_log_company_chain_seq", "audit_log", ["company_id", "chain_seq"], True),
)
_POSTGRES_ONLY = (
    # audit rows written before company_id was a column carry it in details
    """
    UPDATE audit_log AS a SET company_id = c.id
    FROM companies AS c
    WHERE a.company_id IS NULL AND a.details->>'company_id' = c.id::text
    """,
)
_VECTOR_MEMORY = (
    "ALTER TABLE vector_memory ADD COLUMN IF NOT EXISTS company_id INTEGER REFERENCES companies(id)",
    "CREATE INDEX IF NOT EXISTS ix_vector_memory_company_id ON vector_memory (company_id)",
    """
    UPDATE vector_memory AS m SET company_id = c.id
    FROM companies AS c
    WHERE m.company_id IS NULL AND m.metadata->>'company_id' = c.id::text
    """,
)


def _schema() -> tuple[sa.MetaData, list[tuple[str, str, list[str], bool]]]:
    """The schema at revision 0001, frozen: the tables and indexes as the
    models defined them on 2026-11-07. Never derived from the live models, so
    a later migration's change never leaks back into this one."""
    schema = sa.MetaData()
    indexes: list[tuple[str, str, list[str], bool]] = []
    sa.Table(
        "bug_reports",
        schema,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ticket_num", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=False),
        sa.Column("steps", sa.String(), nullable=True),
        sa.Column("expected", sa.String(), nullable=True),
        sa.Column("actual", sa.String(), nullable=True),
        sa.Column("severity", sa.String(), nullable=True),
        sa.Column("category", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("reporter_email", sa.String(), nullable=True),
        sa.Column("assigned_to", sa.String(), nullable=True),
        sa.Column("resolution", sa.String(), nullable=True),
        sa.Column("agent_reply", sa.String(), nullable=True),
        sa.Column("suggested_severity", sa.String(), nullable=True),
        sa.Column("suggested_category", sa.String(), nullable=True),
        sa.Column("triaged_by", sa.String(), nullable=True),
        sa.Column("triaged_at", sa.DateTime(), nullable=True),
        sa.Column("decided_severity", sa.String(), nullable=True),
        sa.Column("decided_category", sa.String(), nullable=True),
        sa.Column("decided_by", sa.String(), nullable=True),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.Column("browser_info", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ticket_num"),
    )
    sa.Table(
        "companies",
        schema,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_name", sa.String(), nullable=False),
        sa.Column("default_scope_size", sa.String(), nullable=False),
        sa.Column("default_sprint_length_days", sa.Integer(), nullable=True),
        sa.Column("autonomy_level", sa.Integer(), nullable=False),
        sa.Column("git_workflow", sa.Boolean(), nullable=False),
        sa.Column("git_base_branch", sa.String(), nullable=True),
        sa.Column("research_hosts", sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    sa.Table(
        "outbox_events",
        schema,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("aggregate_type", sa.String(), nullable=False),
        sa.Column("aggregate_id", sa.String(), nullable=False),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("processed_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    sa.Table(
        "users",
        schema,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("full_name", sa.String(), nullable=False),
        sa.Column("password_hash", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
    )
    sa.Table(
        "agents",
        schema,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("role", sa.String(), nullable=True),
        sa.Column("persona", sa.String(), nullable=True),
        sa.Column("seniority", sa.String(), nullable=False),
        sa.Column("specialty", sa.String(), nullable=False),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("system_prompt", sa.String(), nullable=True),
        sa.Column("token_budget", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    sa.Table(
        "assistant_runtime_state",
        schema,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("assistant_key", sa.String(), nullable=False),
        sa.Column("cooldown_until", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.String(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    indexes.append(
        (
            "ix_assistant_runtime_state_assistant_key",
            "assistant_runtime_state",
            ["assistant_key"],
            False,
        )
    )
    indexes.append(
        (
            "ix_assistant_runtime_state_company_id",
            "assistant_runtime_state",
            ["company_id"],
            False,
        )
    )
    sa.Table(
        "audit_log",
        schema,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=True),
        sa.Column("interaction_id", sa.String(), nullable=True),
        sa.Column("agent_id", sa.String(), nullable=True),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("details", sa.JSON(), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("decision_signature", sa.String(), nullable=True),
        sa.Column("cost_tokens", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("chain_seq", sa.Integer(), nullable=True),
        sa.Column("prev_hash", sa.String(), nullable=True),
        sa.Column("chain_hash", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    indexes.append(("ix_audit_log_company_id", "audit_log", ["company_id"], False))
    indexes.append(
        (
            "ux_audit_log_company_chain_seq",
            "audit_log",
            ["company_id", "chain_seq"],
            True,
        )
    )
    sa.Table(
        "business_units",
        schema,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("unit_name", sa.String(), nullable=False),
        sa.Column("token_quota_limit", sa.Integer(), nullable=False),
        sa.Column("token_quota_used", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    sa.Table(
        "company_members",
        schema,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("human_role", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    sa.Table(
        "github_repo_bindings",
        schema,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("owner", sa.String(), nullable=False),
        sa.Column("repo", sa.String(), nullable=False),
        sa.Column("webhook_secret_encrypted", sa.String(), nullable=False),
        sa.Column("inclusion_rule", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("owner", "repo"),
    )
    sa.Table(
        "mcp_servers",
        schema,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("transport", sa.String(), nullable=False),
        sa.Column("url", sa.String(), nullable=True),
        sa.Column("command", sa.String(), nullable=True),
        sa.Column("args", sa.JSON(), nullable=True),
        sa.Column("cwd", sa.String(), nullable=True),
        sa.Column("secrets", sa.String(), nullable=True),
        sa.Column("roles", sa.JSON(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("timeout_seconds", sa.Float(), nullable=True),
        sa.Column("created_by", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("company_id", "name"),
    )
    indexes.append(("ix_mcp_servers_company_id", "mcp_servers", ["company_id"], False))
    sa.Table(
        "project_credentials",
        schema,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("credential_type", sa.String(), nullable=False),
        sa.Column("access_level", sa.String(), nullable=False),
        sa.Column("encrypted_value", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    sa.Table(
        "project_discovery",
        schema,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("project_path", sa.String(), nullable=False),
        sa.Column("detected_stack", sa.String(), nullable=True),
        sa.Column("suggested_team", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    sa.Table(
        "specifications",
        schema,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("summary", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("supersedes_id", sa.Integer(), nullable=True),
        sa.Column("requirements", sa.JSON(), nullable=True),
        sa.Column("quality", sa.JSON(), nullable=True),
        sa.Column("constraints", sa.JSON(), nullable=True),
        sa.Column("questions", sa.JSON(), nullable=True),
        sa.Column("sources", sa.JSON(), nullable=True),
        sa.Column("drafted_by", sa.String(), nullable=True),
        sa.Column("approved_by", sa.String(), nullable=True),
        sa.Column("approved_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_id"],
            ["specifications.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    indexes.append(
        ("ix_specifications_company_id", "specifications", ["company_id"], False)
    )
    sa.Table(
        "agent_runs",
        schema,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("external_ref", sa.String(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("producer", sa.String(), nullable=False),
        sa.Column("model", sa.String(), nullable=True),
        sa.Column("agent_role", sa.String(), nullable=True),
        sa.Column("subject", sa.JSON(), nullable=True),
        sa.Column("claims", sa.JSON(), nullable=True),
        sa.Column("artifact_text", sa.String(), nullable=True),
        sa.Column("artifact_digest", sa.String(), nullable=False),
        sa.Column("artifact_size", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("ingested_at", sa.DateTime(), nullable=False),
        sa.Column("audit_log_id", sa.Integer(), nullable=True),
        sa.Column("suggested_decision", sa.String(), nullable=True),
        sa.Column("suggested_category", sa.String(), nullable=True),
        sa.Column("suggested_reason", sa.String(), nullable=True),
        sa.Column("reviewed_by", sa.String(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["audit_log_id"],
            ["audit_log.id"],
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("company_id", "external_ref", "version"),
    )
    sa.Table(
        "architectures",
        schema,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("specification_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("overview", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("supersedes_id", sa.Integer(), nullable=True),
        sa.Column("modules", sa.JSON(), nullable=True),
        sa.Column("interfaces", sa.JSON(), nullable=True),
        sa.Column("decisions", sa.JSON(), nullable=True),
        sa.Column("conventions", sa.JSON(), nullable=True),
        sa.Column("questions", sa.JSON(), nullable=True),
        sa.Column("sources", sa.JSON(), nullable=True),
        sa.Column("drafted_by", sa.String(), nullable=True),
        sa.Column("approved_by", sa.String(), nullable=True),
        sa.Column("approved_at", sa.DateTime(), nullable=True),
        sa.Column("written_path", sa.String(), nullable=True),
        sa.Column("written_sha256", sa.String(), nullable=True),
        sa.Column("written_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
        ),
        sa.ForeignKeyConstraint(
            ["specification_id"],
            ["specifications.id"],
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_id"],
            ["architectures.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    indexes.append(
        ("ix_architectures_company_id", "architectures", ["company_id"], False)
    )
    indexes.append(
        (
            "ix_architectures_specification_id",
            "architectures",
            ["specification_id"],
            False,
        )
    )
    sa.Table(
        "sprints",
        schema,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("business_unit_id", sa.Integer(), nullable=True),
        sa.Column("sprint_name", sa.String(), nullable=False),
        sa.Column("start_date", sa.String(), nullable=True),
        sa.Column("scope_size", sa.String(), nullable=False),
        sa.Column("duration_days", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("goal", sa.String(), nullable=True),
        sa.Column("pause_reason", sa.String(), nullable=True),
        sa.Column("specification_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["business_unit_id"],
            ["business_units.id"],
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
        ),
        sa.ForeignKeyConstraint(
            ["specification_id"],
            ["specifications.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    sa.Table(
        "agent_run_approvals",
        schema,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("agent_run_id", sa.Integer(), nullable=False),
        sa.Column("approver_name", sa.String(), nullable=False),
        sa.Column("decision", sa.String(), nullable=False),
        sa.Column("feedback", sa.String(), nullable=True),
        sa.Column("rejection_category", sa.String(), nullable=True),
        sa.Column("decided_at", sa.DateTime(), nullable=False),
        sa.Column("audit_log_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["agent_run_id"],
            ["agent_runs.id"],
        ),
        sa.ForeignKeyConstraint(
            ["audit_log_id"],
            ["audit_log.id"],
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    sa.Table(
        "backlog_items",
        schema,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("sprint_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("priority", sa.String(), nullable=False),
        sa.Column("assignee", sa.String(), nullable=True),
        sa.Column("comments", sa.JSON(), nullable=True),
        sa.Column("covers", sa.JSON(), nullable=True),
        sa.Column("depends_on", sa.JSON(), nullable=True),
        sa.Column("kind", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["sprint_id"],
            ["sprints.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    sa.Table(
        "procedures",
        schema,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("body", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("supersedes_id", sa.Integer(), nullable=True),
        sa.Column("proposed_by", sa.String(), nullable=False),
        sa.Column("rationale", sa.String(), nullable=True),
        sa.Column("source_sprint_id", sa.Integer(), nullable=True),
        sa.Column("baseline", sa.JSON(none_as_null=True), nullable=True),
        sa.Column("outcome", sa.JSON(none_as_null=True), nullable=True),
        sa.Column("adopted_after_sprint_id", sa.Integer(), nullable=True),
        sa.Column("decided_by", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.Column("retired_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
        ),
        sa.ForeignKeyConstraint(
            ["source_sprint_id"],
            ["sprints.id"],
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_id"],
            ["procedures.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    indexes.append(("ix_procedures_company_id", "procedures", ["company_id"], False))
    sa.Table(
        "run_publications",
        schema,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("agent_run_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(), nullable=True),
        sa.Column("branch", sa.String(), nullable=False),
        sa.Column("base", sa.String(), nullable=False),
        sa.Column("commit", sa.String(), nullable=False),
        sa.Column("url", sa.String(), nullable=True),
        sa.Column("how", sa.String(), nullable=False),
        sa.Column("updated", sa.Boolean(), nullable=False),
        sa.Column("published_by", sa.String(), nullable=False),
        sa.Column("published_by_user_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("audit_log_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["agent_run_id"],
            ["agent_runs.id"],
        ),
        sa.ForeignKeyConstraint(
            ["audit_log_id"],
            ["audit_log.id"],
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
        ),
        sa.ForeignKeyConstraint(
            ["published_by_user_id"],
            ["users.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    indexes.append(
        (
            "ix_run_publications_agent_run_id",
            "run_publications",
            ["agent_run_id"],
            False,
        )
    )
    indexes.append(
        ("ix_run_publications_company_id", "run_publications", ["company_id"], False)
    )
    sa.Table(
        "run_reviews",
        schema,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("agent_run_id", sa.Integer(), nullable=False),
        sa.Column("event", sa.String(), nullable=False),
        sa.Column("body", sa.String(), nullable=True),
        sa.Column("outcome", sa.String(), nullable=False),
        sa.Column("author_name", sa.String(), nullable=False),
        sa.Column("author_user_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("audit_log_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["agent_run_id"],
            ["agent_runs.id"],
        ),
        sa.ForeignKeyConstraint(
            ["audit_log_id"],
            ["audit_log.id"],
        ),
        sa.ForeignKeyConstraint(
            ["author_user_id"],
            ["users.id"],
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    indexes.append(
        ("ix_run_reviews_agent_run_id", "run_reviews", ["agent_run_id"], False)
    )
    indexes.append(("ix_run_reviews_company_id", "run_reviews", ["company_id"], False))
    sa.Table(
        "verifications",
        schema,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("agent_run_id", sa.Integer(), nullable=False),
        sa.Column("verdict", sa.String(), nullable=False),
        sa.Column("checks", sa.JSON(), nullable=True),
        sa.Column("evidence", sa.JSON(), nullable=True),
        sa.Column("observed_at", sa.DateTime(), nullable=False),
        sa.Column("audit_log_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["agent_run_id"],
            ["agent_runs.id"],
        ),
        sa.ForeignKeyConstraint(
            ["audit_log_id"],
            ["audit_log.id"],
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    sa.Table(
        "agent_messages",
        schema,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("sprint_id", sa.Integer(), nullable=True),
        sa.Column("backlog_item_id", sa.Integer(), nullable=True),
        sa.Column("from_name", sa.String(), nullable=False),
        sa.Column("from_role", sa.String(), nullable=False),
        sa.Column("to_role", sa.String(), nullable=True),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("body", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["backlog_item_id"],
            ["backlog_items.id"],
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
        ),
        sa.ForeignKeyConstraint(
            ["sprint_id"],
            ["sprints.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    indexes.append(
        (
            "ix_agent_messages_backlog_item_id",
            "agent_messages",
            ["backlog_item_id"],
            False,
        )
    )
    indexes.append(
        ("ix_agent_messages_company_id", "agent_messages", ["company_id"], False)
    )
    indexes.append(
        ("ix_agent_messages_sprint_id", "agent_messages", ["sprint_id"], False)
    )
    sa.Table(
        "review_comments",
        schema,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("agent_run_id", sa.Integer(), nullable=False),
        sa.Column("path", sa.String(), nullable=True),
        sa.Column("line", sa.Integer(), nullable=True),
        sa.Column("side", sa.String(), nullable=False),
        sa.Column("body", sa.String(), nullable=False),
        sa.Column("author_name", sa.String(), nullable=False),
        sa.Column("author_user_id", sa.Integer(), nullable=True),
        sa.Column("review_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.Column("source", sa.String(), nullable=True),
        sa.Column("external_id", sa.String(), nullable=True),
        sa.Column("external_url", sa.String(), nullable=True),
        sa.Column("external_state", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(
            ["agent_run_id"],
            ["agent_runs.id"],
        ),
        sa.ForeignKeyConstraint(
            ["author_user_id"],
            ["users.id"],
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
        ),
        sa.ForeignKeyConstraint(
            ["review_id"],
            ["run_reviews.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    indexes.append(
        ("ix_review_comments_agent_run_id", "review_comments", ["agent_run_id"], False)
    )
    indexes.append(
        ("ix_review_comments_company_id", "review_comments", ["company_id"], False)
    )
    indexes.append(
        ("ix_review_comments_external_id", "review_comments", ["external_id"], False)
    )
    return schema, indexes


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())
    schema, indexes = _schema()
    for table in schema.sorted_tables:
        if table.name not in existing_tables:
            table.create(bind)
            for name, table_name, columns, unique in indexes:
                if table_name == table.name:
                    op.create_index(name, table_name, columns, unique=unique)
            continue
        present = {c["name"] for c in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in present:
                continue
            # Added nullable: an existing row has no value for it, and the
            # application fills it. The model's NOT NULL holds for new rows
            # through the ORM, as it did under the old patches.
            op.add_column(
                table.name,
                sa.Column(column.name, column.type, nullable=True),
            )
            if column.foreign_keys:
                fk = next(iter(column.foreign_keys))
                op.create_foreign_key(
                    None,
                    table.name,
                    fk.column.table.name,
                    [column.name],
                    [fk.column.name],
                )
    inspector = sa.inspect(bind)
    if bind.dialect.name == "postgresql":
        for table_name, column_name in _MADE_OPTIONAL:
            if not inspector.has_table(table_name):
                continue
            column = next(
                (
                    c
                    for c in inspector.get_columns(table_name)
                    if c["name"] == column_name
                ),
                None,
            )
            if column is None:
                continue
            if not column["nullable"]:
                op.alter_column(table_name, column_name, nullable=True)
            if column.get("default") is not None:
                op.alter_column(table_name, column_name, server_default=None)
        for name, table_name, columns, unique in _INDEXES:
            if table_name not in existing_tables or not inspector.has_table(table_name):
                continue
            if name in {i["name"] for i in inspector.get_indexes(table_name)}:
                continue
            op.create_index(name, table_name, columns, unique=unique)
        for statement in _POSTGRES_ONLY:
            op.execute(sa.text(statement))
        if inspector.has_table("vector_memory"):
            for statement in _VECTOR_MEMORY:
                op.execute(sa.text(statement))


def downgrade() -> None:
    # The baseline has nothing before it: a downgrade drops the schema this
    # revision defines, and only that (vector_memory and the rest of
    # init.sql's tables are not the migrations' to drop).
    bind = op.get_bind()
    schema, _indexes = _schema()
    for table in reversed(schema.sorted_tables):
        table.drop(bind, checkfirst=True)
