"""The schema's version (``app/migrations.py``, ``backend/alembic/``): the
migrations build the models' schema from nothing and the two agree; a
database created by the old startup path (tables present, columns missing)
is converged, not rebuilt; the backend refuses a database that is not at
head and names both versions; ``DB_AUTO_UPGRADE`` runs the migrations first.
On a SQLite file: the migration is dialect-neutral where it creates and
adds, and Postgres-only where it repeats the old startup's fixes."""

from __future__ import annotations

from pathlib import Path

import database
import migrations
import pytest
from sqlalchemy import inspect, text
from sqlmodel import SQLModel, create_engine

HEAD = migrations.head_revision()


def _baseline_schema():
    """The schema as 0001 froze it: what an older backend's database holds,
    without the columns later migrations add."""
    import importlib.util

    path = migrations.BACKEND_DIR / "alembic" / "versions" / "0001_baseline.py"
    spec = importlib.util.spec_from_file_location("baseline_0001", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    schema, indexes = module._schema()
    import sqlalchemy as sa

    for name, table_name, columns, unique in indexes:
        table = schema.tables[table_name]
        sa.Index(name, *(table.c[c] for c in columns), unique=unique)
    return schema


@pytest.fixture
def db_file(tmp_path, monkeypatch):
    path = tmp_path / "schema.sqlite"
    url = f"sqlite:///{path}"
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", url)
    return create_engine(url)


def test_the_migrations_build_the_models_schema_from_nothing(db_file):
    assert migrations.current_revision(db_file) is None
    head = migrations.upgrade_to_head(db_file)
    assert head == HEAD and migrations.current_revision(db_file) == head
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
    baseline = _baseline_schema()
    baseline.create_all(
        db_file,
        tables=[t for t in baseline.sorted_tables if t.name not in skipped],
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
    # The old startup's DEFAULT FALSE: the existing row gets false, not NULL.
    assert row[0] == "Acme" and row[1] in (0, False)
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
    assert migrations.current_revision(db_file) == HEAD


def test_the_backend_refuses_a_database_that_is_not_at_head(db_file):
    with pytest.raises(
        migrations.SchemaBehindError, match=f"at unversioned and the code needs {HEAD}"
    ):
        migrations.require_schema_at_head(db_file)
    _baseline_schema().create_all(db_file)
    with db_file.begin() as connection:
        connection.execute(
            text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
        )
        connection.execute(text("INSERT INTO alembic_version VALUES ('0000')"))
    with pytest.raises(
        migrations.SchemaBehindError, match=f"at 0000 and the code needs {HEAD}"
    ):
        migrations.require_schema_at_head(db_file)
    with db_file.begin() as connection:
        connection.execute(
            text("UPDATE alembic_version SET version_num = :v"), {"v": HEAD}
        )
    assert migrations.require_schema_at_head(db_file) == HEAD


def test_startup_runs_the_migrations_only_when_asked(db_file, monkeypatch):
    from config import settings

    monkeypatch.setattr(database, "engine", db_file)
    monkeypatch.setattr(settings, "DB_AUTO_UPGRADE", False)
    with pytest.raises(migrations.SchemaBehindError):
        database.prepare_database()
    monkeypatch.setattr(settings, "DB_AUTO_UPGRADE", True)
    database.prepare_database()
    assert migrations.current_revision(db_file) == HEAD
    monkeypatch.setattr(settings, "DB_AUTO_UPGRADE", False)
    database.prepare_database()  # at head now: nothing to do


def test_the_migration_scripts_have_one_head_and_the_ini_points_at_them():
    assert migrations.head_revision() == HEAD == "0040"
    assert migrations.ALEMBIC_INI.is_file()
    assert (
        migrations.BACKEND_DIR / "alembic" / "versions" / "0001_baseline.py"
    ).is_file()


def test_the_baseline_is_frozen_a_later_model_change_does_not_leak_into_it(
    db_file, monkeypatch
):
    # A table and a column the models gain after 0001 must come from their
    # own migration, never from the baseline; otherwise that migration would
    # fail on a fresh database where 0001 had already created them.
    import sqlalchemy as sa

    extra = sa.Table(
        "later_table", SQLModel.metadata, sa.Column("id", sa.Integer, primary_key=True)
    )
    later_column = sa.Column("later_column", sa.String, nullable=True)
    SQLModel.metadata.tables["companies"].append_column(later_column)
    try:
        migrations.upgrade_to_head(db_file)
    finally:
        SQLModel.metadata.remove(extra)
        companies = SQLModel.metadata.tables["companies"]
        companies._columns.remove(later_column)
    inspector = inspect(db_file)
    assert "later_table" not in inspector.get_table_names()
    assert "later_column" not in {c["name"] for c in inspector.get_columns("companies")}
    source = (
        migrations.BACKEND_DIR / "alembic" / "versions" / "0001_baseline.py"
    ).read_text()
    imports = [
        line for line in source.splitlines() if line.startswith(("import ", "from "))
    ]
    assert not any("models" in line or "sqlmodel" in line for line in imports), imports


def test_a_legacy_sqlite_database_missing_a_foreign_key_column_converges(db_file):
    # sprints.specification_id references specifications: SQLite cannot add
    # that constraint in place, so the baseline adds it through a batch.
    import sqlalchemy as sa

    legacy = sa.MetaData()
    for table in _baseline_schema().sorted_tables:
        if table.name == "sprints":
            sa.Table(
                "sprints",
                legacy,
                *[c.copy() for c in table.columns if c.name != "specification_id"],
            )
        else:
            table.to_metadata(legacy)
    legacy.create_all(db_file)
    migrations.upgrade_to_head(db_file)
    inspector = inspect(db_file)
    assert "specification_id" in {c["name"] for c in inspector.get_columns("sprints")}
    assert any(
        fk["referred_table"] == "specifications"
        for fk in inspector.get_foreign_keys("sprints")
    )


def test_a_new_migration_can_be_generated_and_the_baseline_cannot_be_undone(
    db_file, tmp_path
):
    from alembic import command

    migrations.upgrade_to_head(db_file)
    config = migrations.alembic_config()
    versions = migrations.BACKEND_DIR / "alembic" / "versions"
    config.set_main_option("version_locations", f"{versions} {tmp_path}")
    script = command.revision(
        config, message="nothing yet", autogenerate=True, version_path=str(tmp_path)
    )
    generated = Path(script.path).read_text()
    assert "down_revision" in generated and f"'{HEAD}'" in generated
    with (
        pytest.raises(NotImplementedError, match="restore a backup"),
        db_file.begin() as connection,
    ):
        config.attributes["connection"] = connection
        command.downgrade(config, "base")


def test_tables_init_sql_made_get_the_baseline_indexes_they_lack(db_file):
    # A table that existed before 0001 (init.sql's) had only the indexes the
    # old startup added by hand; 0003 creates the rest of the baseline's.
    baseline = _baseline_schema()
    for table in baseline.sorted_tables:
        for index in list(table.indexes):
            table.indexes.discard(index)
    baseline.create_all(db_file)
    assert not inspect(db_file).get_indexes("specifications")
    migrations.upgrade_to_head(db_file)
    names = {i["name"] for i in inspect(db_file).get_indexes("specifications")}
    assert "ix_specifications_company_id" in names
    assert migrations.schema_differences(db_file) == []
