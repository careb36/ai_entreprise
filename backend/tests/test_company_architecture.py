"""The architecture is the company's, versioned (issue #329, finding F3 of
``docs/retrospectives/2026-11-self-hosting.md``): one current architecture per
company, each approved revision a new version with the earlier ones kept and
readable, a revision for a new specification that keeps the covers of the
earlier ones, a check and a coverage that read every specification a version
covers, a pack that documents every approved specification, and a migration
that turns the existing architectures into versions
(``services/planning/arch_versions.py``, ``routers/company_architecture.py``,
``alembic/versions/0038_company_architecture.py``).
"""

from __future__ import annotations

import secrets
from datetime import datetime

import migrations
import pytest
from models import Company, Sprint
from models.architecture import Architecture
from models.specification import Specification
from services.planning import arch_author, arch_check, arch_versions, spec_check
from services.planning.project_docs import collect, log_docs, scope_docs
from sqlalchemy import create_engine, text
from test_architectures import GOOD_ARCH
from test_specifications import GOOD

SECOND = {
    "title": "Saved views",
    "summary": "A person saves a filtered view of the board.",
    "requirements": [
        {
            "title": "Save a view",
            "description": "A filter is saved under a name.",
            "criteria": [
                {"given": "a filter", "when": "it is saved", "then": "it is listed"}
            ],
        }
    ],
    "quality": [{"attribute": "performance", "target": "a view opens in 300 ms"}],
    "constraints": [],
}


def _company(session) -> Company:
    company = Company(company_name=f"Versions {secrets.token_hex(3)}")
    session.add(company)
    session.commit()
    return company


def _spec(session, company, record) -> Specification:
    spec = Specification(
        company_id=company.id,
        status="approved",
        approved_at=datetime(2026, 11, 1),
        **spec_check.normalize(record),
    )
    session.add(spec)
    session.commit()
    return spec


def _first_version(client, spec) -> dict:
    created = client.post(
        "/v1/architectures", json={"specification_id": spec.id, **GOOD_ARCH}
    )
    assert created.status_code == 201, created.text
    approved = client.post(f"/v1/architectures/{created.json()['id']}/approve")
    assert approved.status_code == 200, approved.text
    return approved.json()


def _second_version(client, company, spec) -> dict:
    """The revision for *spec*: opened by company, its covers written, approved."""
    opened = client.post(
        f"/v1/companies/{company.id}/architecture/revisions",
        json={"specification_id": spec.id},
    )
    assert opened.status_code == 201, opened.text
    draft = opened.json()
    modules = [
        {k: m[k] for k in ("name", "responsibility", "path", "depends_on")}
        | {"covers": ["REQ-1"] if m["name"] == "Store" else []}
        for m in draft["modules"]
    ]
    body = {
        k: draft[k]
        for k in ("title", "overview", "interfaces", "decisions", "conventions")
    }
    edited = client.put(
        f"/v1/architectures/{draft['id']}", json={**body, "modules": modules}
    )
    assert edited.status_code == 200, edited.text
    approved = client.post(f"/v1/architectures/{draft['id']}/approve")
    assert approved.status_code == 200, approved.text
    return approved.json()


# --- the versions through the API -------------------------------------------------


def test_a_revision_for_a_new_specification_is_the_next_version_with_earlier_covers_kept(
    sqlite_session, test_client
):
    company = _company(sqlite_session)
    first_spec = _spec(sqlite_session, company, GOOD)
    second_spec = _spec(sqlite_session, company, SECOND)
    v1 = _first_version(test_client, first_spec)
    assert (v1["version"], v1["is_current"]) == (1, True)
    assert v1["specification_ids"] == [first_spec.id]

    opened = test_client.post(
        f"/v1/companies/{company.id}/architecture/revisions",
        json={"specification_id": second_spec.id},
    ).json()
    assert (opened["version"], opened["status"], opened["supersedes_id"]) == (
        2,
        "draft",
        v1["id"],
    )
    api = next(m for m in opened["modules"] if m["name"] == "Board API")
    assert api["covers"] == []
    assert api["earlier_covers"] == {str(first_spec.id): ["REQ-1", "REQ-2"]}
    # A second revision while one is open is refused, naming it.
    again = test_client.post(
        f"/v1/companies/{company.id}/architecture/revisions",
        json={"specification_id": second_spec.id},
    )
    assert again.status_code == 409
    assert again.json()["detail"]["architecture_id"] == opened["id"]
    # Discard it for the flow below: the helper opens its own.
    sqlite_session.delete(sqlite_session.get(Architecture, opened["id"]))
    sqlite_session.commit()

    v2 = _second_version(test_client, company, second_spec)
    assert (v2["version"], v2["status"], v2["is_current"]) == (2, "approved", True)
    assert v2["specification_ids"] == [second_spec.id, first_spec.id]
    store = next(m for m in v2["modules"] if m["name"] == "Store")
    api = next(m for m in v2["modules"] if m["name"] == "Board API")
    assert store["covers"] == ["REQ-1"]
    # The edit named no earlier covers: the module kept the ones it had.
    assert api["earlier_covers"] == {str(first_spec.id): ["REQ-1", "REQ-2"]}
    assert v2["check"]["blocking"] == 0
    coverage = {c["specification_id"]: c for c in v2["coverage"]}
    assert coverage[first_spec.id]["uncovered"] == []
    assert coverage[second_spec.id]["requirements"] == [
        {"id": "REQ-1", "modules": ["MOD-2"]}
    ]

    sqlite_session.expire_all()
    assert sqlite_session.get(Architecture, v1["id"]).status == "superseded"
    assert sqlite_session.get(Specification, second_spec.id).architecture_id == v2["id"]
    assert sqlite_session.get(Specification, first_spec.id).architecture_id == v1["id"]

    company_view = test_client.get(f"/v1/companies/{company.id}/architecture").json()
    assert company_view["current"]["id"] == v2["id"]
    assert [v["version"] for v in company_view["versions"]] == [2, 1]
    assert company_view["draft"] is None
    old = test_client.get(f"/v1/companies/{company.id}/architecture/versions/1").json()
    assert (old["id"], old["status"]) == (v1["id"], "superseded")
    assert (
        test_client.get(
            f"/v1/companies/{company.id}/architecture/versions/9"
        ).status_code
        == 404
    )
    listed = test_client.get(f"/v1/architectures?specification_id={first_spec.id}")
    assert [a["version"] for a in listed.json()["architectures"]] == [2, 1]

    document = test_client.get(f"/v1/architectures/{v2['id']}/document").text
    assert "It also covers the earlier specifications *Task board*" in document
    assert f"- Covers in *Task board* ({first_spec.id}): REQ-1, REQ-2" in document
    spec_view = test_client.get(f"/v1/specifications/{second_spec.id}").json()
    assert (spec_view["architecture_id"], spec_view["architecture_version"]) == (
        v2["id"],
        2,
    )


def test_each_specification_reads_its_own_covers_from_the_current_version(
    sqlite_session, test_client
):
    company = _company(sqlite_session)
    first_spec = _spec(sqlite_session, company, GOOD)
    second_spec = _spec(sqlite_session, company, SECOND)
    _first_version(test_client, first_spec)
    v2 = _second_version(test_client, company, second_spec)

    first = arch_author.content_for(sqlite_session, first_spec.id)
    second = arch_author.content_for(sqlite_session, second_spec.id)
    assert {m["name"]: m["covers"] for m in first["modules"]}["Board API"] == [
        "REQ-1",
        "REQ-2",
    ]
    assert {m["name"]: m["covers"] for m in second["modules"]}["Store"] == ["REQ-1"]
    assert all("earlier_covers" not in m for m in first["modules"])
    assert arch_author.approved_for(sqlite_session, first_spec.id).id == v2["id"]
    # A task of the first specification is given its own modules.
    sprint = Sprint(
        company_id=company.id, sprint_name="s", specification_id=first_spec.id
    )
    sqlite_session.add(sprint)
    sqlite_session.commit()
    section = arch_author.context_for(sqlite_session, sprint, {"covers": ["REQ-2.1"]})
    assert "Board API" in section and "Web client" not in section


def test_a_draft_written_for_a_new_specification_becomes_the_next_version(
    sqlite_session, test_client
):
    company = _company(sqlite_session)
    first_spec = _spec(sqlite_session, company, GOOD)
    second_spec = _spec(sqlite_session, company, SECOND)
    v1 = _first_version(test_client, first_spec)
    created = test_client.post(
        "/v1/architectures",
        json={
            "specification_id": second_spec.id,
            **GOOD_ARCH,
            "modules": [{**m, "covers": []} for m in GOOD_ARCH["modules"]],
        },
    ).json()
    assert (created["version"], created["supersedes_id"]) == (2, v1["id"])
    api = next(m for m in created["modules"] if m["name"] == "Board API")
    assert api["earlier_covers"] == {str(first_spec.id): ["REQ-1", "REQ-2"]}
    # The per-version revise route takes the specification too.
    sqlite_session.delete(sqlite_session.get(Architecture, created["id"]))
    sqlite_session.commit()
    revised = test_client.post(
        f"/v1/architectures/{v1['id']}/revise",
        json={"specification_id": second_spec.id},
    )
    assert revised.status_code == 201, revised.text
    assert (revised.json()["version"], revised.json()["specification_id"]) == (
        2,
        second_spec.id,
    )


def test_the_check_reads_every_specification_the_version_covers():
    arch = arch_check.normalize(
        {
            **GOOD_ARCH,
            "modules": [
                {
                    **GOOD_ARCH["modules"][0],
                    "earlier_covers": {"7": ["REQ-1.2", "REQ-9"]},
                },
                {
                    **GOOD_ARCH["modules"][1],
                    "earlier_covers": {"8": ["REQ-1"], "x": []},
                },
                GOOD_ARCH["modules"][2],
            ],
        }
    )
    assert arch["modules"][0]["earlier_covers"] == {"7": ["REQ-1", "REQ-9"]}
    assert arch["modules"][1]["earlier_covers"] == {"8": ["REQ-1"]}
    assert "earlier_covers" not in arch["modules"][2]
    earlier = {"7": {"status": "approved", "requirement_ids": ["REQ-1", "REQ-2"]}}
    found = {
        (f["severity"], f["where"], f["code"])
        for f in arch_check.check(arch, ["REQ-1", "REQ-2"], earlier)["findings"]
    }
    assert ("blocking", "MOD-1", "unknown_requirement") in found  # REQ-9 of 7
    assert ("blocking", "MOD-2", "unknown_requirement") in found  # 8 is not known
    assert ("warning", "7:REQ-2", "uncovered_requirement") in found
    # Without earlier specifications the check is what it was.
    assert arch_check.check(arch, ["REQ-1", "REQ-2"])["ok"] is True


def test_rebase_moves_the_own_covers_and_takes_the_targets_back():
    modules = [{"name": "A", "covers": ["REQ-1"], "earlier_covers": {"3": ["REQ-4"]}}]
    assert arch_versions.rebase(modules, 5, 3, {3}) == [
        {"name": "A", "covers": ["REQ-4"], "earlier_covers": {"5": ["REQ-1"]}}
    ]
    # A revision of the same specification (6 supersedes 5) keeps the covers.
    assert arch_versions.rebase(modules, 5, 6, {6, 5}) == [
        {"name": "A", "covers": ["REQ-1"], "earlier_covers": {"3": ["REQ-4"]}}
    ]
    assert arch_versions.rebase(modules, 5, 5, {5}) == modules


def test_a_cover_of_another_companys_specification_blocks(sqlite_session, test_client):
    company, other = _company(sqlite_session), _company(sqlite_session)
    spec = _spec(sqlite_session, company, GOOD)
    foreign = _spec(sqlite_session, other, SECOND)
    created = test_client.post(
        "/v1/architectures",
        json={
            "specification_id": spec.id,
            **GOOD_ARCH,
            "modules": [
                {
                    **GOOD_ARCH["modules"][0],
                    "earlier_covers": {str(foreign.id): ["REQ-1"]},
                },
                *GOOD_ARCH["modules"][1:],
            ],
        },
    ).json()
    assert created["check"]["blocking"] == 1
    refused = test_client.post(f"/v1/architectures/{created['id']}/approve")
    assert refused.status_code == 409


# --- the pack documents every approved specification ------------------------------


def test_the_pack_documents_every_approved_specification(sqlite_session, test_client):
    company = _company(sqlite_session)
    first_spec = _spec(sqlite_session, company, GOOD)
    second_spec = _spec(sqlite_session, company, SECOND)
    _first_version(test_client, first_spec)
    v2 = _second_version(test_client, company, second_spec)
    sqlite_session.add(
        Sprint(
            company_id=company.id, sprint_name="views", specification_id=second_spec.id
        )
    )
    sqlite_session.commit()
    snapshot = collect.snapshot(sqlite_session, company)
    assert [s["id"] for s in snapshot["specifications"]] == [
        first_spec.id,
        second_spec.id,
    ]
    assert snapshot["architecture"]["id"] == v2["id"]
    scope = scope_docs.scope(snapshot)
    assert "Task board" in scope["markdown"] and "Saved views" in scope["markdown"]
    assert "specifications" in scope["inputs"]
    trace = log_docs.traceability(snapshot)["markdown"]
    assert trace.count("Specification ") >= 2
    wbs = scope_docs.wbs(snapshot)["markdown"]
    assert "## 1 Task board" in wbs and "## 2 Saved views" in wbs

    # With one approved specification the documents read as before.
    single = {**snapshot, "specifications": snapshot["specifications"][-1:]}
    assert "specifications" not in scope_docs.scope(single)["inputs"]


# --- the migration ------------------------------------------------------------------


@pytest.fixture
def db_file(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'versions.sqlite'}"
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", url)
    return create_engine(url)


def _to(engine, revision: str, down: bool = False) -> None:
    from alembic import command

    config = migrations.alembic_config()
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        (command.downgrade if down else command.upgrade)(config, revision)


def test_the_migration_makes_the_existing_architectures_versions_of_the_company(
    db_file,
):
    previous = migrations.alembic_config()
    from alembic.script import ScriptDirectory

    down = ScriptDirectory.from_config(previous).get_revision("0038").down_revision
    _to(db_file, down)
    with db_file.begin() as c:
        for sid in (1, 2):
            c.execute(
                text(
                    "INSERT INTO specifications (id, company_id, title, summary, status, version, requirements, quality, constraints, questions, sources, created_at, updated_at) "
                    "VALUES (:id, 1, 's', '', 'approved', 1, '[]', '[]', '[]', '[]', '[]', '2026-11-01', '2026-11-01')"
                ),
                {"id": sid},
            )
        # Spec 2's architecture was approved first, spec 1's later; a draft too.
        rows = [
            (10, 1, "approved", "2026-11-03 10:00:00"),
            (11, 2, "approved", "2026-11-02 10:00:00"),
            (12, 1, "draft", None),
        ]
        for aid, sid, status, approved_at in rows:
            c.execute(
                text(
                    "INSERT INTO architectures (id, company_id, specification_id, title, overview, status, version, modules, interfaces, decisions, conventions, questions, sources, approved_at, created_at, updated_at) "
                    "VALUES (:id, 1, :sid, 'a', '', :status, 1, '[]', '[]', '[]', '[]', '[]', '[]', :at, '2026-11-01', '2026-11-01')"
                ),
                {"id": aid, "sid": sid, "status": status, "at": approved_at},
            )
    _to(db_file, "0038")
    with db_file.connect() as c:
        got = {
            r[0]: (r[1], r[2], r[3])
            for r in c.execute(
                text("SELECT id, version, status, supersedes_id FROM architectures")
            )
        }
        specs = {
            r[0]: r[1]
            for r in c.execute(text("SELECT id, architecture_id FROM specifications"))
        }
    assert got == {
        11: (1, "superseded", None),
        10: (2, "approved", 11),
        12: (3, "draft", None),
    }
    assert specs == {1: 10, 2: 11}
    _to(db_file, down, down=True)
    with db_file.connect() as c:
        back = {
            r[0]: (r[1], r[2])
            for r in c.execute(text("SELECT id, version, status FROM architectures"))
        }
    # Numbered per specification again, each with its approved version.
    assert back == {10: (1, "approved"), 12: (2, "draft"), 11: (1, "approved")}
