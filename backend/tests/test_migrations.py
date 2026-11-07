"""The schema's version (``app/migrations.py``, ``backend/alembic/``): the
migrations build the models' schema from nothing and the two agree; a
database created by the old startup path (tables present, columns missing)
is converged, not rebuilt; the backend refuses a database that is not at
head and names both versions; ``DB_AUTO_UPGRADE`` runs the migrations first.
On a SQLite file: the migration is dialect-neutral where it creates and
adds, and Postgres-only where it repeats the old startup's fixes."""

from __future__ import annotations

import database
import migrations
import pytest
from sqlalchemy import inspect, text
from sqlmodel import SQLModel, create_engine


@pytest.fixture
def db_file(tmp_path, monkeypatch):
    path = tmp_path / "schema.sqlite"
    url = f"sqlite:///{path}"
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", url)
    return create_engine(url)


def test_the_migrations_build_the_models_schema_from_nothing(db_file):
    assert migrations.current_revision(db_file) is None
    head = migrations.upgrade_to_head(db_file)
    assert head == "0001" and migrations.current_revision(db_file) == head
    tables = set(inspect(db_file).get_table_names())
    assert {t.name for t in SQLModel.metadata.sorted_tables} <= tables
    assert "alembic_version" in tables
    # What `alembic check` reports: nothing.
    assert migrations.schema_differences(db_file) == []


def test_a_database_from_the_old_startup_is_converged_not_rebuilt(db_file):
    # The old path: tables created from the models of the day, some columns
    # and some tables not yet known. Here: every table but two, and the
    # companies table without two later columns.
    skipped = {"architectures", "mcp_servers"}
    SQLModel.metadata.create_all(
        db_file,
        tables=[t for t in SQLModel.metadata.sorted_tables if t.name not in skipped],
    )
    with db_file.begin() as connection:
        connection.execute(text("ALTER TABLE companies DROP COLUMN git_base_branch"))
        connection.execute(text("ALTER TABLE companies DROP COLUMN git_workflow"))
        connection.execute(
            text(
                "INSERT INTO companies (company_name, autonomy_level, default_scope_size) VALUES ('Acme', 2, 'M')"
            )
        )
    migrations.upgrade_to_head(db_file)
    inspector = inspect(db_file)
    assert skipped <= set(inspector.get_table_names())
    companies = {c["name"] for c in inspector.get_columns("companies")}
    assert {"git_base_branch", "git_workflow"} <= companies
    with db_file.connect() as connection:
        row = connection.execute(
            text("SELECT company_name, git_workflow FROM companies")
        ).one()
    assert row == ("Acme", None)
    # A column the model requires was added nullable (an existing row has no
    # value for it); that is the one difference left with the models, and it
    # is named, not hidden. git_base_branch is optional in the model too.
    differences = [
        (op[0], op[2], op[3])
        for group in migrations.schema_differences(db_file)
        for op in (group if isinstance(group, list) else [group])
    ]
    assert differences == [("modify_nullable", "companies", "git_workflow")]
    # Running it again changes nothing.
    migrations.upgrade_to_head(db_file)
    assert migrations.current_revision(db_file) == "0001"


def test_the_backend_refuses_a_database_that_is_not_at_head(db_file):
    with pytest.raises(
        migrations.SchemaBehindError, match="at unversioned and the code needs 0001"
    ):
        migrations.require_schema_at_head(db_file)
    SQLModel.metadata.create_all(db_file)
    with db_file.begin() as connection:
        connection.execute(
            text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
        )
        connection.execute(text("INSERT INTO alembic_version VALUES ('0000')"))
    with pytest.raises(
        migrations.SchemaBehindError, match="at 0000 and the code needs 0001"
    ):
        migrations.require_schema_at_head(db_file)
    with db_file.begin() as connection:
        connection.execute(text("UPDATE alembic_version SET version_num = '0001'"))
    assert migrations.require_schema_at_head(db_file) == "0001"


def test_startup_runs_the_migrations_only_when_asked(db_file, monkeypatch):
    monkeypatch.setattr(database, "engine", db_file)
    monkeypatch.delenv("DB_AUTO_UPGRADE", raising=False)
    with pytest.raises(migrations.SchemaBehindError):
        database.prepare_database()
    monkeypatch.setenv("DB_AUTO_UPGRADE", "true")
    database.prepare_database()
    assert migrations.current_revision(db_file) == "0001"
    monkeypatch.delenv("DB_AUTO_UPGRADE")
    database.prepare_database()  # at head now: nothing to do


def test_the_migration_scripts_have_one_head_and_the_ini_points_at_them():
    assert migrations.head_revision() == "0001"
    assert migrations.ALEMBIC_INI.is_file()
    assert (
        migrations.BACKEND_DIR / "alembic" / "versions" / "0001_baseline.py"
    ).is_file()
