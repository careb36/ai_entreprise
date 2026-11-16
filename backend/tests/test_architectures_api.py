"""The architecture through the API (``routers/architectures.py``): a Manager
writes a draft and a Viewer reads it, the Architect drafts from the approved
specification, asks, and a person approves, and a revision supersedes the
approved one with the prompts following it. The shared rows are in
``architecture_helpers``.
"""

import json

import pytest
from models import BacklogItem, Sprint
from models.company import ProjectDiscovery
from models.specification import Specification
from services.planning import (
    arch_author,
    spec_author,
    spec_check,
    spec_planning,
)

from architecture_helpers import GOOD_ARCH, _approved_spec, _company
from factories import enforcement  # noqa: F401 - the fixture, by name
from factories import member_headers as _member
from test_specifications import GOOD

# --- the routes --------------------------------------------------------------


@pytest.mark.usefixtures("enforcement")
def test_a_manager_writes_a_draft_a_viewer_reads_it_and_nobody_else(
    sqlite_session, test_client
):
    company, other = _company(sqlite_session), _company(sqlite_session)
    spec = _approved_spec(sqlite_session, company)
    manager = _member(sqlite_session, company.id, "Manager")
    viewer = _member(sqlite_session, company.id, "Viewer")
    outsider = _member(sqlite_session, other.id, "Manager")
    body = {"specification_id": spec.id, **GOOD_ARCH}
    assert (
        test_client.post("/v1/architectures", json=body, headers=viewer).status_code
        == 403
    )
    assert (
        test_client.post("/v1/architectures", json=body, headers=outsider).status_code
        == 403
    )
    created = test_client.post("/v1/architectures", json=body, headers=manager)
    assert created.status_code == 201, created.text
    arch = created.json()
    assert arch["status"] == "draft" and arch["check"]["ok"] is True
    assert arch["interfaces"][0]["module_id"] == "MOD-1"
    listed = test_client.get(
        f"/v1/architectures?specification_id={spec.id}", headers=viewer
    ).json()
    assert [a["id"] for a in listed["architectures"]] == [arch["id"]]
    assert (
        test_client.get(f"/v1/architectures/{arch['id']}", headers=outsider).status_code
        == 403
    )
    document = test_client.get(
        f"/v1/architectures/{arch['id']}/document", headers=viewer
    )
    assert document.status_code == 200 and document.text.startswith("# Board service")


@pytest.mark.usefixtures("enforcement")
def test_the_architect_drafts_from_the_approved_specification_asks_and_a_person_approves(
    sqlite_session, test_client, monkeypatch
):
    company = _company(sqlite_session)
    spec = _approved_spec(sqlite_session, company)
    draft_spec = Specification(company_id=company.id, **spec_check.normalize(GOOD))
    sqlite_session.add(draft_spec)
    sqlite_session.add(
        ProjectDiscovery(
            company_id=company.id,
            project_path="/nowhere/app",
            detected_stack=json.dumps({"language": "Python"}),
        )
    )
    sqlite_session.commit()
    manager = _member(sqlite_session, company.id, "Manager")
    calls = []

    def fake_ask(model, system_prompt, prompt, **kwargs):
        calls.append((kwargs.get("role"), prompt))
        draft = {**GOOD_ARCH, "questions": ["Which database?"]}
        if "Answers to your questions" not in prompt:
            draft["decisions"] = [
                {"title": "Pick a store", "context": "c", "decision": ""}
            ]
        return json.dumps(draft)

    monkeypatch.setattr(spec_author, "ask", fake_ask)
    # The Architect's call runs in a thread without the session: what it needs
    # is plain values, read before.
    prepared = arch_author.prepare(sqlite_session, spec)
    assert set(prepared) == {
        "specification_id",
        "company_id",
        "model",
        "system_prompt",
        "prompt",
        "bu_id",
        "questions",
        "research_hosts",
        "project_path",
    }
    assert prepared["specification_id"] == spec.id and "REQ-1.1" in prepared["prompt"]
    assert (
        test_client.post(
            "/v1/architectures/draft",
            json={"specification_id": draft_spec.id},
            headers=manager,
        ).status_code
        == 409
    )
    drafted = test_client.post(
        "/v1/architectures/draft", json={"specification_id": spec.id}, headers=manager
    )
    assert drafted.status_code == 200, drafted.text
    arch = drafted.json()
    assert arch["drafted_by"] == "Architect" and calls[0][0] == "Architect"
    assert (
        "REQ-2.1: given a task in todo" in calls[0][1]
        and "Primary language: Python" in calls[0][1]
    )
    blocking = {
        (f["where"], f["code"])
        for f in arch["check"]["findings"]
        if f["severity"] == "blocking"
    }
    assert blocking == {("ADR-1", "no_decision"), ("Q1", "open_question")}
    refused = test_client.post(
        f"/v1/architectures/{arch['id']}/approve", headers=manager
    )
    assert refused.status_code == 409 and [
        f["where"] for f in refused.json()["detail"]["findings"]
    ] == ["ADR-1", "Q1"]

    answered = test_client.post(
        f"/v1/architectures/{arch['id']}/questions/0",
        json={"answer": "SQLite"},
        headers=manager,
    ).json()
    assert answered["questions"] == [
        {"question": "Which database?", "answer": "SQLite"}
    ]
    revised = test_client.post(
        "/v1/architectures/draft",
        json={"specification_id": spec.id, "architecture_id": arch["id"]},
        headers=manager,
    ).json()
    assert revised["id"] == arch["id"] and "A: SQLite" in calls[1][1]
    assert (
        revised["check"]["ok"] is True and revised["questions"][0]["answer"] == "SQLite"
    )
    approved = test_client.post(
        f"/v1/architectures/{arch['id']}/approve", headers=manager
    ).json()
    assert approved["status"] == "approved"
    assert (
        test_client.put(
            f"/v1/architectures/{arch['id']}", json=GOOD_ARCH, headers=manager
        ).status_code
        == 409
    )

    monkeypatch.setattr(spec_author, "ask", lambda *a, **k: "no json here")
    assert (
        test_client.post(
            "/v1/architectures/draft",
            json={"specification_id": spec.id},
            headers=manager,
        ).status_code
        == 502
    )
    assert (
        len(
            test_client.get(
                f"/v1/architectures?specification_id={spec.id}", headers=manager
            ).json()["architectures"]
        )
        == 1
    )


@pytest.mark.usefixtures("enforcement")
def test_a_revision_supersedes_the_approved_one_and_the_prompts_follow_it(
    sqlite_session, test_client
):
    company = _company(sqlite_session)
    spec = _approved_spec(sqlite_session, company)
    manager = _member(sqlite_session, company.id, "Manager")
    v1 = test_client.post(
        "/v1/architectures",
        json={"specification_id": spec.id, **GOOD_ARCH},
        headers=manager,
    ).json()
    assert (
        test_client.post(
            f"/v1/architectures/{v1['id']}/revise", headers=manager
        ).status_code
        == 409
    )
    test_client.post(f"/v1/architectures/{v1['id']}/approve", headers=manager)
    v2 = test_client.post(f"/v1/architectures/{v1['id']}/revise", headers=manager)
    assert v2.status_code == 201 and (
        v2.json()["version"],
        v2.json()["supersedes_id"],
    ) == (2, v1["id"])
    assert (
        test_client.post(
            f"/v1/architectures/{v1['id']}/revise", headers=manager
        ).status_code
        == 409
    )
    edited = test_client.put(
        f"/v1/architectures/{v2.json()['id']}",
        json={**GOOD_ARCH, "title": "Board service v2"},
        headers=manager,
    ).json()
    test_client.post(f"/v1/architectures/{edited['id']}/approve", headers=manager)
    statuses = {
        a["id"]: a["status"]
        for a in test_client.get(
            f"/v1/architectures?specification_id={spec.id}", headers=manager
        ).json()["architectures"]
    }
    assert statuses == {v1["id"]: "superseded", edited["id"]: "approved"}

    sprint = Sprint(company_id=company.id, sprint_name="S", specification_id=spec.id)
    sqlite_session.add(sprint)
    sqlite_session.commit()
    item = BacklogItem(sprint_id=sprint.id, title="Move", covers=["REQ-2.1"])
    sqlite_session.add(item)
    sqlite_session.commit()
    context = spec_planning.requirements_context(sqlite_session.get_bind(), item.id)
    assert (
        "## Requirements this task covers" in context and "## Architecture" in context
    )
    assert "MOD-1: Board API" in context and "MOD-3: Web client" not in context
    assert "Board service v2" not in context  # the title is not what a task needs
    assert arch_author.planning_context(sqlite_session, spec.id).startswith(
        "\n\n## Architecture (approved)"
    )
