"""The specification through the API (``routers/specifications.py``): a
Manager writes a draft and a Viewer reads it, the PO drafts from a brief and
asks, a person answers and approves, an unusable answer changes nothing, a
revision supersedes the approved version, and the diff and impact of a
revision. The shared rows are in ``specification_helpers``.
"""

import json

import pytest
from services.planning import spec_author

from factories import enforcement  # noqa: F401 - the fixture, by name
from factories import member_headers as _member
from specification_helpers import CRITERION, GOOD, _company

# --- the routes --------------------------------------------------------------


@pytest.mark.usefixtures("enforcement")
def test_a_manager_writes_a_draft_a_viewer_reads_it_and_nobody_else(
    sqlite_session, test_client
):
    company, other = _company(sqlite_session), _company(sqlite_session)
    manager = _member(sqlite_session, company.id, "Manager")
    viewer = _member(sqlite_session, company.id, "Viewer")
    outsider = _member(sqlite_session, other.id, "Manager")
    body = {"company_id": company.id, **GOOD}
    assert (
        test_client.post("/v1/specifications", json=body, headers=viewer).status_code
        == 403
    )
    assert (
        test_client.post("/v1/specifications", json=body, headers=outsider).status_code
        == 403
    )
    created = test_client.post("/v1/specifications", json=body, headers=manager)
    assert created.status_code == 201, created.text
    spec = created.json()
    assert spec["status"] == "draft" and spec["version"] == 1
    assert spec["requirements"][1]["criteria"][0]["id"] == "REQ-2.1"
    assert spec["check"]["ok"] is True
    assert spec["coverage"] == {
        "criteria": [
            {
                "id": c,
                "requirement_id": c.split(".")[0],
                "tasks": [],
                "tests": [],
                "status": "uncovered",
                "test": f"test_{c.lower().replace('-', '_').replace('.', '_')}_…",
                "verified": False,
                "last_result": None,
            }
            for c in ("REQ-1.1", "REQ-1.2", "REQ-2.1")
        ],
        "total": 3,
        "covered": 0,
        "done": 0,
        "verified": 0,
    }
    listed = test_client.get(
        f"/v1/specifications?company_id={company.id}", headers=viewer
    ).json()
    assert [s["id"] for s in listed["specifications"]] == [spec["id"]]
    assert (
        test_client.get(
            f"/v1/specifications/{spec['id']}", headers=outsider
        ).status_code
        == 403
    )
    assert (
        test_client.get(
            f"/v1/specifications?company_id={company.id}", headers=outsider
        ).status_code
        == 403
    )


@pytest.mark.usefixtures("enforcement")
def test_the_po_drafts_from_a_brief_asks_and_a_person_answers_and_approves(
    sqlite_session, test_client, monkeypatch
):
    company = _company(sqlite_session)
    manager = _member(sqlite_session, company.id, "Manager")
    prompts = []

    def fake_ask(model, system_prompt, prompt, **kwargs):
        prompts.append(prompt)
        draft = {
            **GOOD,
            "quality": [{"attribute": "performance", "target": "fast"}],
            "questions": ["Which columns does the board have?"],
        }
        if "Answers to your questions" in prompt:
            draft["quality"] = [
                {"attribute": "performance", "target": "renders in under 300 ms"}
            ]
            draft["requirements"] = GOOD["requirements"] + [
                {
                    "title": "Columns",
                    "description": "The board has the columns the team chose.",
                    "criteria": [CRITERION],
                }
            ]
        return json.dumps(draft)

    monkeypatch.setattr(spec_author, "ask", fake_ask)
    drafted = test_client.post(
        "/v1/specifications/draft",
        json={"company_id": company.id, "brief": "A task board for small teams."},
        headers=manager,
    )
    assert drafted.status_code == 200, drafted.text
    spec = drafted.json()
    assert spec["drafted_by"] == "PO" and "A task board for small teams." in prompts[0]
    assert spec["questions"] == [
        {"question": "Which columns does the board have?", "answer": None}
    ]
    blocking = {
        (f["where"], f["code"])
        for f in spec["check"]["findings"]
        if f["severity"] == "blocking"
    }
    assert blocking == {("NFR-1", "no_number"), ("Q1", "open_question")}

    refused = test_client.post(
        f"/v1/specifications/{spec['id']}/approve", headers=manager
    )
    assert refused.status_code == 409
    assert [f["where"] for f in refused.json()["detail"]["findings"]] == ["NFR-1", "Q1"]

    answered = test_client.post(
        f"/v1/specifications/{spec['id']}/questions/0",
        json={"answer": "todo, doing, done"},
        headers=manager,
    ).json()
    assert answered["questions"][0]["answer"] == "todo, doing, done"
    assert (
        test_client.post(
            f"/v1/specifications/{spec['id']}/questions/5",
            json={"answer": "x"},
            headers=manager,
        ).status_code
        == 404
    )

    assert (
        test_client.post(
            "/v1/specifications/draft",
            json={"company_id": company.id, "brief": "  "},
            headers=manager,
        ).status_code
        == 422
    )
    # A revision needs no new brief: the answers are what the PO works from.
    revised = test_client.post(
        "/v1/specifications/draft",
        json={"company_id": company.id, "specification_id": spec["id"]},
        headers=manager,
    ).json()
    assert revised["id"] == spec["id"]
    assert "A: todo, doing, done" in prompts[1]
    assert "## Brief from the person asking" not in prompts[1]
    assert revised["questions"] == [
        {
            "question": "Which columns does the board have?",
            "answer": "todo, doing, done",
        }
    ]
    assert [r["id"] for r in revised["requirements"]] == ["REQ-1", "REQ-2", "REQ-3"]
    assert revised["check"]["ok"] is True

    approved = test_client.post(
        f"/v1/specifications/{spec['id']}/approve", headers=manager
    ).json()
    assert approved["status"] == "approved" and approved["approved_by"].startswith(
        "manager-"
    )
    assert (
        test_client.put(
            f"/v1/specifications/{spec['id']}", json=GOOD, headers=manager
        ).status_code
        == 409
    )


@pytest.mark.usefixtures("enforcement")
def test_an_unusable_answer_leaves_the_draft_as_it_was(
    sqlite_session, test_client, monkeypatch
):
    company = _company(sqlite_session)
    manager = _member(sqlite_session, company.id, "Manager")
    spec = test_client.post(
        "/v1/specifications", json={"company_id": company.id, **GOOD}, headers=manager
    ).json()
    monkeypatch.setattr(spec_author, "ask", lambda *a, **k: "I would build a board.")
    refused = test_client.post(
        "/v1/specifications/draft",
        json={
            "company_id": company.id,
            "brief": "a board",
            "specification_id": spec["id"],
        },
        headers=manager,
    )
    assert refused.status_code == 502
    again = test_client.get(f"/v1/specifications/{spec['id']}", headers=manager).json()
    assert (
        again["requirements"] == spec["requirements"]
        and again["drafted_by"] == spec["drafted_by"]
    )

    def failing(*a, **k):
        raise spec_author.SpecAuthorError("LLM call failed")

    monkeypatch.setattr(spec_author, "ask", failing)
    assert (
        test_client.post(
            "/v1/specifications/draft",
            json={"company_id": company.id, "brief": "a board"},
            headers=manager,
        ).status_code
        == 502
    )
    assert (
        len(
            test_client.get(
                f"/v1/specifications?company_id={company.id}", headers=manager
            ).json()["specifications"]
        )
        == 1
    )


@pytest.mark.usefixtures("enforcement")
def test_a_revision_is_a_new_version_that_supersedes_the_approved_one(
    sqlite_session, test_client
):
    company = _company(sqlite_session)
    manager = _member(sqlite_session, company.id, "Manager")
    # Segregation of duties (#543): who drafts does not approve.
    approver = _member(sqlite_session, company.id, "Manager")
    v1 = test_client.post(
        "/v1/specifications", json={"company_id": company.id, **GOOD}, headers=manager
    ).json()
    assert (
        test_client.post(
            f"/v1/specifications/{v1['id']}/revise", headers=manager
        ).status_code
        == 409
    )
    test_client.post(f"/v1/specifications/{v1['id']}/approve", headers=approver)
    v2 = test_client.post(f"/v1/specifications/{v1['id']}/revise", headers=manager)
    assert v2.status_code == 201
    v2 = v2.json()
    assert (v2["version"], v2["supersedes_id"], v2["status"]) == (2, v1["id"], "draft")
    assert (
        test_client.post(
            f"/v1/specifications/{v1['id']}/revise", headers=manager
        ).status_code
        == 409
    )
    edited = test_client.put(
        f"/v1/specifications/{v2['id']}",
        json={**GOOD, "title": "Task board v2"},
        headers=manager,
    ).json()
    assert edited["title"] == "Task board v2"
    test_client.post(f"/v1/specifications/{v2['id']}/approve", headers=approver)
    statuses = {
        s["id"]: s["status"]
        for s in test_client.get(
            f"/v1/specifications?company_id={company.id}", headers=manager
        ).json()["specifications"]
    }
    assert statuses == {v1["id"]: "superseded", v2["id"]: "approved"}


# --- diff and impact of a revision -----------------------------------------


@pytest.mark.usefixtures("enforcement")
def test_specification_diff_and_impact_report_changed_criteria(
    sqlite_session, test_client
):
    company = _company(sqlite_session)
    manager = _member(sqlite_session, company.id, "Manager")
    # Segregation of duties (#543): who drafts does not approve.
    approver = _member(sqlite_session, company.id, "Manager")
    v1 = test_client.post(
        "/v1/specifications", json={"company_id": company.id, **GOOD}, headers=manager
    ).json()
    test_client.post(f"/v1/specifications/{v1['id']}/approve", headers=approver)

    # A revision that rewords one criterion and drops a requirement.
    import copy

    revised_good = copy.deepcopy(GOOD)
    revised_good["requirements"] = [copy.deepcopy(GOOD["requirements"][0])]
    revised_good["requirements"][0]["criteria"][0]["then"] = (
        "the list has two rows per task"
    )
    v2 = test_client.post(
        f"/v1/specifications/{v1['id']}/revise", headers=manager
    ).json()
    test_client.put(
        f"/v1/specifications/{v2['id']}", json=revised_good, headers=manager
    )
    test_client.post(f"/v1/specifications/{v2['id']}/approve", headers=approver)

    diff = test_client.get(
        f"/v1/specifications/{v2['id']}/diff?against={v1['id']}", headers=manager
    ).json()
    assert diff["from_version"] == 1 and diff["to_version"] == 2
    d = diff["diff"]
    # The "Move a task" requirement was removed.
    assert "REQ-2" in d["requirements"]["removed"]
    # The reworded criterion is reported as changed.
    assert "REQ-1.1" in d["changed_criterion_ids"]

    # Impact lists no tasks (none planned yet) but reports the changed criteria.
    impact = test_client.get(
        f"/v1/specifications/{v2['id']}/impact", headers=manager
    ).json()
    assert impact["previous_id"] == v1["id"]
    assert "REQ-1.1" in impact["changed_criterion_ids"]
    assert "REQ-2" in impact["changed_requirement_ids"]


# --- HTTP: diff endpoint ----------------------------------------------------


@pytest.mark.usefixtures("enforcement")
def test_diff_endpoint_http(sqlite_session, test_client):
    company = _company(sqlite_session)
    manager = _member(sqlite_session, company.id, "Manager")
    # Segregation of duties (#543): who drafts does not approve.
    approver = _member(sqlite_session, company.id, "Manager")
    v1 = test_client.post(
        "/v1/specifications", json={"company_id": company.id, **GOOD}, headers=manager
    ).json()
    test_client.post(f"/v1/specifications/{v1['id']}/approve", headers=approver)
    v2 = test_client.post(
        f"/v1/specifications/{v1['id']}/revise", headers=manager
    ).json()
    test_client.put(
        f"/v1/specifications/{v2['id']}",
        json={**GOOD, "title": "Task board v2"},
        headers=manager,
    )
    test_client.post(f"/v1/specifications/{v2['id']}/approve", headers=approver)

    r = test_client.get(
        f"/v1/specifications/{v2['id']}/diff?against={v1['id']}", headers=manager
    )
    assert r.status_code == 200
    assert r.json()["from_version"] == 1
    assert r.json()["to_version"] == 2

    # Viewer can read.
    viewer = _member(sqlite_session, company.id, "Viewer")
    r2 = test_client.get(
        f"/v1/specifications/{v2['id']}/diff?against={v1['id']}", headers=viewer
    )
    assert r2.status_code == 200


# --- HTTP: impact endpoint --------------------------------------------------


@pytest.mark.usefixtures("enforcement")
def test_impact_endpoint_http(sqlite_session, test_client):
    company = _company(sqlite_session)
    manager = _member(sqlite_session, company.id, "Manager")
    # Segregation of duties (#543): who drafts does not approve.
    approver = _member(sqlite_session, company.id, "Manager")
    v1 = test_client.post(
        "/v1/specifications", json={"company_id": company.id, **GOOD}, headers=manager
    ).json()
    test_client.post(f"/v1/specifications/{v1['id']}/approve", headers=approver)
    v2 = test_client.post(
        f"/v1/specifications/{v1['id']}/revise", headers=manager
    ).json()
    test_client.put(
        f"/v1/specifications/{v2['id']}",
        json={**GOOD, "title": "Task board v2"},
        headers=manager,
    )
    test_client.post(f"/v1/specifications/{v2['id']}/approve", headers=approver)

    r = test_client.get(f"/v1/specifications/{v2['id']}/impact", headers=manager)
    assert r.status_code == 200
    assert "changed_criterion_ids" in r.json()
