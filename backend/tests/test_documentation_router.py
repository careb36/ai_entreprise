"""The documentation pack's routes (issue #530, ``routers/documentation.py``):
a Manager compiles, a Viewer reads, only an Owner approves and only while the
pack matches the records, an approved pack is written to ``docs/project/``
through the daemon with the sha256 it read back, and the snapshot reads real
records (task events with their chain hash) without naming people.
"""

import json
import secrets

import pytest
from factories import enforcement  # noqa: F401 - the fixture, by name
from factories import member_headers as _member
from models import BacklogItem, Company, Sprint
from models.architecture import Architecture
from models.company import Agent, CompanyMember, ProjectDiscovery, User
from models.specification import Specification
from services.planning import arch_check, spec_check
from services.planning.project_docs import collect
from services.planning.project_docs import pack as packs
from services.tasks import task_events
from test_architectures import GOOD_ARCH, _Daemon
from test_specifications import GOOD


def _company(session):
    company = Company(company_name=f"Docs {secrets.token_hex(3)}", autonomy_level=2)
    session.add(company)
    session.commit()
    return company


def _records(session, company):
    spec = Specification(
        company_id=company.id,
        status="approved",
        drafted_by="PO",
        **spec_check.normalize(GOOD),
    )
    session.add(spec)
    session.commit()
    arch = Architecture(
        company_id=company.id,
        specification_id=spec.id,
        status="approved",
        drafted_by="Architect",
        **arch_check.normalize(GOOD_ARCH),
    )
    sprint = Sprint(company_id=company.id, sprint_name="One", specification_id=spec.id)
    session.add_all([arch, sprint, Agent(company_id=company.id, role="Developer")])
    session.add(
        ProjectDiscovery(
            company_id=company.id,
            project_path="/home/ana/app",
            detected_stack=json.dumps({"language": "Python", "databases": ["sqlite"]}),
        )
    )
    session.commit()
    item = BacklogItem(
        sprint_id=sprint.id,
        title="Build the list",
        assignee="Developer",
        covers=["REQ-1.1"],
    )
    session.add(item)
    session.commit()
    task_events.record(
        session,
        item,
        "blocked",
        "QA",
        text="BLOCKED: tests fail",
        author="QA",
        status="blocked",
    )
    session.commit()
    return spec, arch, sprint, item


@pytest.fixture
def no_prose(monkeypatch):
    calls = []

    def fake(request):
        calls.append(request)
        return "The team needs one shared board."

    monkeypatch.setattr(packs, "write_prose", fake)
    return calls


def test_the_snapshot_reads_the_records_and_names_nobody(sqlite_session):
    company = _company(sqlite_session)
    spec, arch, _sprint, _item = _records(sqlite_session, company)
    user = User(
        email=f"o-{secrets.token_hex(4)}@example.com", full_name="O", password_hash="x"
    )
    sqlite_session.add(user)
    sqlite_session.commit()
    sqlite_session.add(
        CompanyMember(company_id=company.id, user_id=user.id, human_role="Owner")
    )
    sqlite_session.commit()
    snap = collect.snapshot(sqlite_session, company)
    assert (
        snap["specification"]["id"] == spec.id
        and snap["specification"]["drafted_by_role"] == "PO"
    )
    assert snap["architecture"]["id"] == arch.id
    assert snap["stack"]["databases"] == ["sqlite"]
    assert snap["sprints"][0]["tasks"][0]["covers"] == ["REQ-1.1"]
    event = snap["events"][0]
    assert (
        event["kind"] == "blocked"
        and event["chain_hash"]
        and "tests fail" in event["reason"]
    )
    assert snap["roles"] == ["Developer"] and snap["members"] == {"Owner": 1}
    assert "@" not in json.dumps(snap)


@pytest.mark.usefixtures("enforcement")
def test_a_manager_compiles_a_viewer_reads_and_only_an_owner_approves(
    sqlite_session, test_client, no_prose
):
    company, other = _company(sqlite_session), _company(sqlite_session)
    _records(sqlite_session, company)
    owner = _member(sqlite_session, company.id, "Owner")
    manager = _member(sqlite_session, company.id, "Manager")
    viewer = _member(sqlite_session, company.id, "Viewer")
    outsider = _member(sqlite_session, other.id, "Owner")
    url = f"/v1/companies/{company.id}/documentation"

    assert test_client.post(url, json={}, headers=viewer).status_code == 403
    assert test_client.post(url, json={}, headers=outsider).status_code == 403
    created = test_client.post(url, json={}, headers=manager)
    assert created.status_code == 201, created.text
    pack = created.json()
    assert (
        pack["status"] == "draft"
        and pack["version"] == 1
        and pack["lint"]["ok"] is True
    )
    assert [d["key"] for d in pack["documents"]][:3] == ["index", "charter", "scope"]
    assert len(pack["diagrams"]) == 3 and pack["diagrams"][0]["mermaid"].startswith(
        "flowchart"
    )
    charter = next(d for d in pack["documents"] if d["key"] == "charter")
    assert charter["claims"] == [{"section": "background", "role": "TechWriter"}]
    assert len(no_prose) == 1

    assert test_client.get(url, headers=viewer).json()["packs"][0]["id"] == pack["id"]
    assert test_client.get(url, headers=outsider).status_code == 403
    got = test_client.get(f"/v1/documentation/{pack['id']}", headers=viewer)
    assert got.status_code == 200 and got.json()["lint"]["ok"] is True
    assert (
        test_client.get(f"/v1/documentation/{pack['id']}", headers=outsider).status_code
        == 403
    )
    text = test_client.get(
        f"/v1/documentation/{pack['id']}/documents/issues", headers=viewer
    )
    assert text.status_code == 200 and text.text.startswith("# Issue log")
    assert "Task T-" in text.text and "BLOCKED: tests fail" in text.text
    assert (
        test_client.get(
            f"/v1/documentation/{pack['id']}/documents/nope", headers=viewer
        ).status_code
        == 404
    )

    approve = f"/v1/documentation/{pack['id']}/approve"
    assert test_client.post(approve, headers=manager).status_code == 403
    assert test_client.post(approve, headers=viewer).status_code == 403
    approved = test_client.post(approve, headers=owner)
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"
    assert test_client.post(approve, headers=owner).status_code == 409

    # A second compile is a new draft; approving it supersedes the first.
    second = test_client.post(url, json={"prose": False}, headers=manager).json()
    assert second["version"] == 2 and len(no_prose) == 1
    assert (
        test_client.post(
            f"/v1/documentation/{second['id']}/approve", headers=owner
        ).status_code
        == 200
    )
    statuses = {
        p["id"]: p["status"]
        for p in test_client.get(url, headers=viewer).json()["packs"]
    }
    assert statuses == {pack["id"]: "superseded", second["id"]: "approved"}


def test_a_pack_that_drifted_from_the_records_cannot_be_approved(
    sqlite_session, test_client, no_prose
):
    company = _company(sqlite_session)
    _spec, _arch, sprint, _item = _records(sqlite_session, company)
    pack = test_client.post(f"/v1/companies/{company.id}/documentation", json={}).json()
    sqlite_session.add(BacklogItem(sprint_id=sprint.id, title="A new task"))
    sqlite_session.commit()
    lint = test_client.get(f"/v1/documentation/{pack['id']}").json()["lint"]
    stale = {d["key"] for d in lint["documents"] if d["stale"]}
    assert {"wbs", "schedule", "raci"} <= stale and "charter" not in stale
    refused = test_client.post(f"/v1/documentation/{pack['id']}/approve")
    assert refused.status_code == 409
    assert refused.json()["detail"]["findings"][0]["severity"] == "blocking"
    fresh = test_client.post(
        f"/v1/companies/{company.id}/documentation", json={}
    ).json()
    assert (
        test_client.post(f"/v1/documentation/{fresh['id']}/approve").status_code == 200
    )
    # The draft it replaced is superseded and cannot be approved any more.
    assert (
        test_client.post(f"/v1/documentation/{pack['id']}/approve").status_code == 409
    )


def test_an_approved_pack_is_written_to_docs_project_with_the_daemons_hashes(
    sqlite_session, test_client, no_prose
):
    from app.main import app

    company = _company(sqlite_session)
    _records(sqlite_session, company)
    pack = test_client.post(f"/v1/companies/{company.id}/documentation", json={}).json()
    write = f"/v1/documentation/{pack['id']}/write"
    assert test_client.post(write).status_code == 409
    test_client.post(f"/v1/documentation/{pack['id']}/approve")
    paths = [d["file"] for d in pack["documents"]]
    assert paths[0] == "docs/project/README.md" and "docs/project/charter.md" in paths
    daemon = _Daemon(
        {
            "ok": True,
            "files": [
                {"path": p, "status": "written", "sha256": "ab" * 32} for p in paths
            ],
        }
    )
    app.state.nats_client = daemon
    written = test_client.post(write)
    assert written.status_code == 200, written.text
    assert [f["path"] for f in written.json()["written_files"]] == paths
    subject, payload = daemon.requests[0]
    assert (
        subject == "workspace.generate" and payload["workspace_root"] == "/home/ana/app"
    )
    assert all(f["overwrite_existing"] for f in payload["files"])
    from models import AuditLog
    from sqlmodel import select

    actions = sqlite_session.exec(
        select(AuditLog.action).where(
            AuditLog.interaction_id == f"documentation-pack:{pack['id']}"
        )
    ).all()
    assert sorted(actions) == [
        "documentation_pack_approved",
        "documentation_pack_written",
    ]

    daemon.reply = {
        "ok": True,
        "files": [{"path": paths[0], "status": "written", "sha256": "ab" * 32}],
    }
    assert test_client.post(write).status_code == 502
    daemon.reply = RuntimeError("nats: no responders available for request")
    assert test_client.post(write).status_code == 503


def test_compiling_with_no_records_says_what_is_missing(
    sqlite_session, test_client, no_prose
):
    company = _company(sqlite_session)
    pack = test_client.post(f"/v1/companies/{company.id}/documentation", json={}).json()
    assert pack["diagrams"] == [] and pack["specification_id"] is None
    assert no_prose == [None]
    charter = next(d for d in pack["documents"] if d["key"] == "charter")
    assert "No source recorded" in charter["markdown"] and charter["missing"] == ["all"]
    assert (
        test_client.post("/v1/companies/999999/documentation", json={}).status_code
        == 404
    )


@pytest.mark.usefixtures("enforcement")
def test_the_pack_follows_the_projects_language_set_by_a_manager(
    sqlite_session, test_client, no_prose
):
    company = _company(sqlite_session)
    _records(sqlite_session, company)
    manager = _member(sqlite_session, company.id, "Manager")
    viewer = _member(sqlite_session, company.id, "Viewer")
    url = f"/v1/companies/{company.id}/doc_language"
    assert test_client.get(url, headers=viewer).json() == {
        "company_id": company.id,
        "doc_language": "en",
        "chosen": False,
    }
    english = test_client.post(
        f"/v1/companies/{company.id}/documentation", json={}, headers=manager
    ).json()
    assert english["language"] == "en"
    assert "Write in English." in no_prose[-1]["prompt"]
    refused = test_client.put(url, json={"doc_language": "es"}, headers=viewer)
    assert refused.status_code == 403
    unknown = test_client.put(url, json={"doc_language": "fr"}, headers=manager)
    assert unknown.status_code == 422
    assert test_client.put(url, json={"doc_language": "es"}, headers=manager).json()[
        "chosen"
    ]
    lint = test_client.get(f"/v1/documentation/{english['id']}", headers=viewer).json()[
        "lint"
    ]
    assert lint["ok"] is False and "now es" in lint["findings"][-1]["message"]
    spanish = test_client.post(
        f"/v1/companies/{company.id}/documentation", json={}, headers=manager
    ).json()
    assert spanish["language"] == "es" and spanish["lint"]["ok"] is True
    assert "in Spanish" in no_prose[-1]["prompt"]
    titles = [d["title"] for d in spanish["documents"]]
    assert titles[:2] == [
        "Documentación del proyecto",
        "Acta de constitución del proyecto",
    ]
    from models import AuditLog
    from sqlmodel import select

    actions = sqlite_session.exec(
        select(AuditLog.action).where(
            AuditLog.interaction_id == f"doc-language:{company.id}"
        )
    ).all()
    assert actions == ["doc_language_updated"]


def test_a_new_company_keeps_the_language_its_creator_chose(sqlite_session):
    from services.loop.company_service import CompanyService

    result = CompanyService(sqlite_session).init_company(
        company_name=f"Lang {secrets.token_hex(3)}", agents=[], doc_language="es"
    )
    company = sqlite_session.get(Company, result["company_id"])
    assert company.doc_language == "es"
