"""Saved views of a sprint's tasks, filtered on the evidence (issue #535).

The filter's contract (``services/contracts/task_filters.py``: the allowlist,
the operators and values per field, the bounds of the tree), its evaluation
from the task projections (``services/tasks/task_filter.py``), the saved
views' access (``services/tasks/saved_views.py``) and the API
(``routers/task_views.py``). The requirements are those of the specification
of #535 (``scripts/self_host/fixtures/issue-535-specification.json``):
REQ-1 the allowlist, REQ-2 agreement with the board, REQ-3 the views' access.
"""

from __future__ import annotations

import json
import secrets
import time
from uuid import uuid4

import auth_tokens
import pytest
from config import settings
from models import (
    AgentRun,
    AuditLog,
    BacklogItem,
    Company,
    CompanyMember,
    SavedView,
    Sprint,
    User,
    Verification,
)
from models.agent_run import TASK_WORKER_PRODUCER
from services.contracts import task_filters
from services.evidence import risk
from services.tasks import task_filter
from sqlalchemy import inspect
from sqlmodel import Session, select

WAITS = [{"author": "System", "text": "Blocked: manual triage needed."}]
RETRIES = [{"author": "System", "text": "Agent error: the call timed out."}]


def _company(session: Session) -> Company:
    company = Company(company_name=f"Views {uuid4().hex[:6]}")
    session.add(company)
    session.commit()
    return company


def _sprint(session: Session, company: Company, name: str = "S") -> Sprint:
    sprint = Sprint(company_id=company.id, sprint_name=name)
    session.add(sprint)
    session.commit()
    return sprint


def _task(session: Session, sprint: Sprint, **fields) -> BacklogItem:
    fields.setdefault("title", f"Task {uuid4().hex[:4]}")
    item = BacklogItem(sprint_id=sprint.id, **fields)
    session.add(item)
    session.commit()
    return item


def _run(session, company, item, verdict, tier=None, producer=TASK_WORKER_PRODUCER):
    earlier = session.exec(
        select(AgentRun).where(AgentRun.external_ref == f"task-{item.id}")
    ).all()
    run = AgentRun(
        company_id=company.id,
        external_ref=f"task-{item.id}",
        version=len(earlier) + 1,
        producer=producer,
        artifact_digest="0" * 64,
        artifact_size=1,
        # What the producer said of its own work: never read as the verdict.
        claims={"verdict": "verified", "risk": {"tier": "low"}},
    )
    session.add(run)
    session.commit()
    checks = [{"name": "risk_tier", "tier": tier}] if tier else []
    session.add(
        Verification(
            company_id=company.id, agent_run_id=run.id, verdict=verdict, checks=checks
        )
    )
    session.commit()
    return run


def _ids(result) -> list[int]:
    return [t["id"] for t in result["tasks"]]


def _query(session, company, raw, **kw):
    return task_filter.query(session, company.id, task_filters.parse_filter(raw), **kw)


# ── The contract ─────────────────────────────────────────────────────────────


def test_the_allowlist_is_the_issues_fields():
    assert set(task_filters.FILTER_FIELDS) == {
        "status",
        "verdict",
        "risk_tier",
        "producer",
        "assignee_role",
        "rework_count",
        "waits_for_person",
        "covers",
        "sprint_id",
    }
    # The risk tiers are the Verifier's, in its order.
    assert task_filters.RISK_TIERS == (risk.RISK_LOW, risk.RISK_MEDIUM, risk.RISK_HIGH)


@pytest.mark.parametrize("field", ["title", "description", "comments", "id; --", 3])
def test_a_field_outside_the_allowlist_is_refused_and_named(field):
    with pytest.raises(task_filters.FilterRefused) as caught:
        task_filters.parse_filter({"and": [{"field": field, "op": "eq", "value": "x"}]})
    assert caught.value.field is not None
    if isinstance(field, str):
        assert caught.value.field == field


@pytest.mark.parametrize(
    "condition",
    [
        {"field": "status", "op": "eq", "value": "archived"},
        {"field": "status", "op": "gte", "value": "done"},
        {"field": "verdict", "op": "eq", "value": "approved"},
        {"field": "risk_tier", "op": "gte", "value": None},
        {"field": "rework_count", "op": "gte", "value": -1},
        {"field": "rework_count", "op": "eq", "value": True},
        {"field": "waits_for_person", "op": "eq", "value": "yes"},
        {"field": "covers", "op": "eq", "value": "REQ-1' OR 1=1"},
        {"field": "sprint_id", "op": "eq", "value": "1; DROP TABLE backlog_items"},
        {"field": "sprint_id", "op": "in", "value": []},
        {"field": "producer", "op": "eq", "value": "x" * 201},
        {"field": "producer", "op": "like", "value": "%"},
        {"field": "status", "op": "eq"},
        {"field": "status", "op": "eq", "value": "done", "sql": "1=1"},
    ],
)
def test_an_operator_or_value_the_field_does_not_take_is_refused(condition):
    with pytest.raises(task_filters.FilterRefused) as caught:
        task_filters.parse_filter({"and": [condition]})
    assert caught.value.field in (None, condition["field"])


def _nested(depth: int) -> dict:
    tree: dict = {"field": "status", "op": "eq", "value": "done"}
    for level in range(depth):
        tree = {("and" if level % 2 else "or"): [tree]}
    return tree


def test_and_or_groups_are_bounded_in_depth_members_and_conditions():
    assert task_filters.parse_filter(_nested(task_filters.MAX_DEPTH))
    with pytest.raises(task_filters.FilterRefused, match="deeper"):
        task_filters.parse_filter(_nested(task_filters.MAX_DEPTH + 1))
    wide = {"and": [{"field": "rework_count", "op": "gte", "value": 0}] * 21}
    with pytest.raises(task_filters.FilterRefused, match="members"):
        task_filters.parse_filter(wide)
    many = {
        "or": [{"and": [{"field": "rework_count", "op": "gte", "value": 0}] * 10}] * 6
    }
    with pytest.raises(task_filters.FilterRefused, match="conditions"):
        task_filters.parse_filter(many)
    for bad in ([], {"and": []}, {"and": [], "or": []}, {"not": [1]}, "status"):
        with pytest.raises(task_filters.FilterRefused):
            task_filters.parse_filter(bad)
    with pytest.raises(task_filters.FilterRefused, match="bytes"):
        task_filters.load("{" + " " * task_filters.MAX_RAW_BYTES + "}")
    with pytest.raises(task_filters.FilterRefused, match="JSON"):
        task_filters.load("{status=done")


def test_matches_reads_and_or_ordered_tiers_and_covered_requirements():
    facts = {
        "status": "blocked",
        "risk_tier": "medium",
        "covers": ["REQ-3.1", "REQ-12"],
        "rework_count": 2,
        "verdict": None,
    }

    def check(raw):
        return task_filters.matches(task_filters.parse_filter(raw), facts)

    assert check({"field": "risk_tier", "op": "gte", "value": "medium"})
    assert not check({"field": "risk_tier", "op": "gte", "value": "high"})
    assert check({"field": "covers", "op": "eq", "value": "REQ-3"})
    assert not check({"field": "covers", "op": "eq", "value": "REQ-1"})  # not REQ-12
    assert check({"field": "covers", "op": "ne", "value": "REQ-1"})
    assert check({"field": "verdict", "op": "eq", "value": None})
    assert check(
        {
            "or": [
                {"field": "status", "op": "eq", "value": "done"},
                {"and": [{"field": "rework_count", "op": "lte", "value": 2}]},
            ]
        }
    )
    assert not check({"field": "rework_count", "op": "gte", "value": 3})


# ── Evaluation from the projections ──────────────────────────────────────────


def test_REQ_1_blocked_and_waiting_for_a_person(sqlite_session):
    company = _company(sqlite_session)
    sprint = _sprint(sqlite_session, company)
    waits = _task(sqlite_session, sprint, status="blocked", comments=WAITS)
    _task(sqlite_session, sprint, status="blocked", comments=RETRIES)
    _task(sqlite_session, sprint, status="done")
    result = _query(
        sqlite_session,
        company,
        {
            "and": [
                {"field": "status", "op": "eq", "value": "blocked"},
                {"field": "waits_for_person", "op": "eq", "value": True},
            ]
        },
    )
    assert _ids(result) == [waits.id] and result["count"] == 1


def test_verdict_and_risk_come_from_the_verifier_never_the_claims(sqlite_session):
    company = _company(sqlite_session)
    sprint = _sprint(sqlite_session, company)
    refuted = _task(sqlite_session, sprint, status="blocked", assignee="Dev")
    verified = _task(sqlite_session, sprint, status="done", assignee="QA")
    never = _task(sqlite_session, sprint)
    _run(sqlite_session, company, refuted, "verified", "low")
    _run(sqlite_session, company, refuted, "refuted", "high", producer="console")
    _run(sqlite_session, company, verified, "verified", "medium")

    def ids(raw):
        return _ids(_query(sqlite_session, company, raw))

    # The latest run decides; the claims say "verified" and "low" on every run.
    assert ids({"field": "verdict", "op": "eq", "value": "refuted"}) == [refuted.id]
    assert ids({"field": "verdict", "op": "eq", "value": None}) == [never.id]
    assert ids({"field": "risk_tier", "op": "gte", "value": "medium"}) == [
        refuted.id,
        verified.id,
    ]
    assert ids({"field": "producer", "op": "eq", "value": "console"}) == [refuted.id]
    # "Dev" is the Developer's older spelling (contracts/roles.canonical).
    assert ids({"field": "assignee_role", "op": "eq", "value": "Developer"}) == [
        refuted.id
    ]
    row = _query(sqlite_session, company, None)["tasks"][0]
    assert (row["verdict"], row["risk_tier"], row["producer"]) == (
        "refuted",
        "high",
        "console",
    )


def test_rework_covers_order_group_and_pages(sqlite_session):
    company = _company(sqlite_session)
    sprint = _sprint(sqlite_session, company)
    rework = [{"author": "QA", "text": "NEEDS WORK: add a test"}]
    a = _task(sqlite_session, sprint, covers=["REQ-3.1"], comments=rework * 2)
    b = _task(sqlite_session, sprint, covers=["REQ-3.2"], comments=rework)
    c = _task(sqlite_session, sprint, covers=["REQ-4.1"], status="weird")
    result = _query(
        sqlite_session,
        company,
        {"field": "covers", "op": "eq", "value": "REQ-3"},
        order_by="-rework_count",
        group_by="status",
    )
    assert _ids(result) == [a.id, b.id]
    assert [t["rework_count"] for t in result["tasks"]] == [2, 1]
    assert result["groups"] == {"todo": 2}
    # An unknown stored status is filtered as the board shows it: To do.
    todo = _query(
        sqlite_session, company, {"field": "status", "op": "eq", "value": "todo"}
    )
    assert c.id in _ids(todo)
    page = _query(sqlite_session, company, None, limit=1, offset=1)
    assert _ids(page) == [b.id] and page["count"] == 3


def test_another_companys_tasks_are_never_returned(sqlite_session):
    mine, theirs = _company(sqlite_session), _company(sqlite_session)
    _task(sqlite_session, _sprint(sqlite_session, theirs), status="done")
    other_sprint = sqlite_session.exec(
        select(Sprint).where(Sprint.company_id == theirs.id)
    ).first()
    raw = {"field": "sprint_id", "op": "eq", "value": other_sprint.id}
    assert _query(sqlite_session, mine, raw)["count"] == 0


def test_the_query_binds_its_values_and_input_never_reaches_sql(sqlite_session):
    company = _company(sqlite_session)
    sprint = _sprint(sqlite_session, company)
    _task(sqlite_session, sprint)
    hostile = "x'; DROP TABLE backlog_items; --"
    result = _query(
        sqlite_session, company, {"field": "producer", "op": "eq", "value": hostile}
    )
    assert result["count"] == 0
    assert "backlog_items" in inspect(sqlite_session.get_bind()).get_table_names()
    # The sprint narrowing is a bound IN list, never text from the request.
    statement = select(BacklogItem).where(BacklogItem.sprint_id.in_([sprint.id]))
    compiled = statement.compile(sqlite_session.get_bind())
    assert "POSTCOMPILE" in str(compiled)
    assert list(compiled.params.values()) == [[sprint.id]]
    # And the module builds no SQL text at all.
    with open(task_filter.__file__) as handle:
        source = handle.read()
    assert "text(" not in source and "execute(" not in source


# ── The board and a view agree (REQ-2) ───────────────────────────────────────


def test_REQ_2_the_board_and_a_view_give_the_same_counts(test_client, sqlite_session):
    company = _company(sqlite_session)
    sprint = _sprint(sqlite_session, company)
    other = _sprint(sqlite_session, company, "Other")
    for status in ["done", "done", "done", "blocked", "todo", "in_review", "odd"]:
        _task(sqlite_session, sprint, status=status)
    _task(sqlite_session, other, status="done")
    board = test_client.get(f"/v1/sprints/{sprint.id}/board").json()
    for status, cards in board["columns"].items():
        raw = {
            "and": [
                {"field": "status", "op": "eq", "value": status},
                {"field": "sprint_id", "op": "eq", "value": sprint.id},
            ]
        }
        view = test_client.get(
            "/v1/tasks",
            params={"company_id": company.id, "filter": json.dumps(raw)},
        ).json()
        assert view["count"] == len(cards), status
        assert sorted(_ids(view)) == sorted(c["id"] for c in cards)
    grouped = test_client.get(
        "/v1/tasks",
        params={
            "company_id": company.id,
            "group_by": "status",
            "filter": json.dumps(
                {"field": "sprint_id", "op": "eq", "value": sprint.id}
            ),
        },
    ).json()
    assert grouped["groups"] == {s: len(c) for s, c in board["columns"].items() if c}
    assert grouped["count"] == board["total"] == 7


def test_the_api_refuses_an_unknown_field_with_422_naming_it(
    test_client, sqlite_session
):
    company = _company(sqlite_session)
    raw = json.dumps({"and": [{"field": "title", "op": "eq", "value": "x"}]})
    response = test_client.get(
        "/v1/tasks", params={"company_id": company.id, "filter": raw}
    )
    assert response.status_code == 422
    assert response.json()["detail"]["field"] == "title"
    for params in ({"order_by": "password"}, {"group_by": "title"}):
        bad = test_client.get("/v1/tasks", params={"company_id": company.id, **params})
        assert bad.status_code == 422


# ── Performance ──────────────────────────────────────────────────────────────


def test_a_filter_over_five_thousand_tasks_answers_within_the_page_budget(
    sqlite_session,
):
    company = _company(sqlite_session)
    sprints = [_sprint(sqlite_session, company, f"S{i}") for i in range(10)]
    statuses = task_filters.STATUSES
    items = [
        BacklogItem(
            sprint_id=sprints[i % 10].id,
            title=f"Task {i}",
            status=statuses[i % 5],
            assignee="Developer",
            comments=WAITS if i % 5 == 4 else [],
            covers=[f"REQ-{i % 7}.1"],
        )
        for i in range(5000)
    ]
    sqlite_session.add_all(items)
    sqlite_session.commit()
    runs = [
        AgentRun(
            company_id=company.id,
            external_ref=f"task-{item.id}",
            producer=TASK_WORKER_PRODUCER,
            artifact_digest="0" * 64,
            artifact_size=1,
        )
        for item in items[::2]
    ]
    sqlite_session.add_all(runs)
    sqlite_session.commit()
    sqlite_session.add_all(
        Verification(
            company_id=company.id,
            agent_run_id=run.id,
            verdict="refuted" if n % 3 else "verified",
            checks=[{"name": "risk_tier", "tier": "high" if n % 4 else "low"}],
        )
        for n, run in enumerate(runs)
    )
    sqlite_session.commit()
    tree = task_filters.parse_filter(
        {
            "and": [
                {"field": "verdict", "op": "eq", "value": "refuted"},
                {"field": "risk_tier", "op": "gte", "value": "medium"},
                {
                    "or": [
                        {"field": "status", "op": "in", "value": ["blocked", "todo"]},
                        {"field": "covers", "op": "eq", "value": "REQ-3"},
                    ]
                },
            ]
        }
    )
    timings = []
    for _ in range(5):
        start = time.perf_counter()
        result = task_filter.query(sqlite_session, company.id, tree, limit=100)
        timings.append(time.perf_counter() - start)
    assert 0 < result["count"] < 5000 and len(result["tasks"]) == 100
    # The page budget of docs/ux/performance_usability_benchmark.md is 400 ms
    # (the Doherty threshold); the specification asks 300 ms for the query.
    assert sorted(timings)[len(timings) // 2] < 0.3, timings


# ── Saved views: access, locks, roles (REQ-3) ────────────────────────────────


@pytest.fixture()
def api(sqlite_session, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "AUTH_REQUIRED", True)
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "audit"))
    key = secrets.token_urlsafe(48)
    monkeypatch.setattr(auth_tokens, "_configured_key", lambda: key)
    company = _company(sqlite_session)
    other = _company(sqlite_session)

    def member(company_id, role):
        user = User(
            email=f"{role.lower()}-{secrets.token_hex(5)}@example.com",
            full_name=role,
            password_hash="x",
        )
        sqlite_session.add(user)
        sqlite_session.commit()
        sqlite_session.add(
            CompanyMember(company_id=company_id, user_id=user.id, human_role=role)
        )
        sqlite_session.commit()
        token = auth_tokens.create_access_token(user_id=user.id, email=user.email)
        return {"Authorization": f"Bearer {token}"}

    return {
        "company": company,
        "viewer": member(company.id, "Viewer"),
        "viewer2": member(company.id, "Viewer"),
        "manager": member(company.id, "Manager"),
        "owner": member(company.id, "Owner"),
        "outsider": member(other.id, "Manager"),
    }


REFUTED = {"and": [{"field": "verdict", "op": "eq", "value": "refuted"}]}


def test_REQ_3_a_private_view_is_listed_only_by_its_author(test_client, api):
    url = f"/v1/companies/{api['company'].id}/views"
    created = test_client.post(
        url, json={"name": "My refuted", "filters": REFUTED}, headers=api["viewer"]
    )
    assert created.status_code == 201, created.text
    view = created.json()
    assert view["access"] == "private" and view["mine"] and view["valid"]
    assert view["layout"] == "list" and view["visible_columns"]

    def names(who):
        return [
            v["name"] for v in test_client.get(url, headers=api[who]).json()["views"]
        ]

    assert names("viewer") == ["My refuted"]
    assert names("viewer2") == [] and names("manager") == [] and names("owner") == []
    # Another member reads it as missing, and cannot filter through it.
    assert (
        test_client.get(f"/v1/views/{view['id']}", headers=api["manager"]).status_code
        == 404
    )
    through = test_client.get(
        "/v1/tasks",
        params={"company_id": api["company"].id, "view_id": view["id"]},
        headers=api["viewer2"],
    )
    assert through.status_code == 404
    assert test_client.get(url, headers=api["outsider"]).status_code == 403
    assert test_client.get(url).status_code == 401


def test_REQ_3_a_locked_company_view_refuses_a_viewer(test_client, sqlite_session, api):
    url = f"/v1/companies/{api['company'].id}/views"
    body = {"name": "Refuted", "filters": REFUTED, "access": "company", "locked": True}
    # Sharing or locking takes a Manager.
    assert test_client.post(url, json=body, headers=api["viewer"]).status_code == 403
    created = test_client.post(url, json=body, headers=api["manager"])
    assert created.status_code == 201
    view = created.json()
    listed = test_client.get(url, headers=api["viewer"]).json()["views"]
    assert [v["id"] for v in listed] == [view["id"]] and not listed[0]["can_edit"]

    change = {"filters": {"and": [{"field": "status", "op": "eq", "value": "done"}]}}
    refused = test_client.put(
        f"/v1/views/{view['id']}", json=change, headers=api["viewer"]
    )
    assert refused.status_code == 403
    assert (
        test_client.delete(f"/v1/views/{view['id']}", headers=api["viewer"]).status_code
        == 403
    )
    sqlite_session.expire_all()
    assert sqlite_session.get(SavedView, view["id"]).filters == REFUTED

    # A Manager (the author or not) changes it, and unlocks it.
    changed = test_client.put(
        f"/v1/views/{view['id']}", json=change, headers=api["owner"]
    )
    assert changed.status_code == 200 and changed.json()["filters"] == change["filters"]
    unlocked = test_client.put(
        f"/v1/views/{view['id']}", json={"locked": False}, headers=api["manager"]
    )
    assert unlocked.json()["locked"] is False
    # Unlocked, a company view is still its author's or a Manager's to change.
    again = test_client.put(
        f"/v1/views/{view['id']}", json=change, headers=api["viewer"]
    )
    assert again.status_code == 403
    assert (
        test_client.delete(
            f"/v1/views/{view['id']}", headers=api["manager"]
        ).status_code
        == 204
    )
    actions = {
        row.action
        for row in sqlite_session.exec(
            select(AuditLog).where(
                AuditLog.interaction_id == f"company:{api['company'].id}:views"
            )
        ).all()
    }
    assert actions == {"view_created", "view_updated", "view_deleted"}


def test_a_view_filters_tasks_and_preferences_stay_apart(
    test_client, sqlite_session, api
):
    company = api["company"]
    sprint = _sprint(sqlite_session, company)
    bad = _task(sqlite_session, sprint, status="blocked")
    _task(sqlite_session, sprint, status="done")
    _run(sqlite_session, company, bad, "refuted", "high")
    view = test_client.post(
        f"/v1/companies/{company.id}/views",
        json={
            "name": "R",
            "filters": REFUTED,
            "access": "company",
            "locked": True,
            "order_by": "-risk_tier",
            "group_by": "status",
            "layout": "board",
        },
        headers=api["manager"],
    ).json()
    result = test_client.get(
        "/v1/tasks",
        params={"company_id": company.id, "view_id": view["id"]},
        headers=api["viewer"],
    ).json()
    assert _ids(result) == [bad.id] and result["groups"] == {"blocked": 1}

    prefs = {
        "collapsed_groups": ["done"],
        "density": "compact",
        "column_widths": {"title": 320},
    }
    put = test_client.put(
        f"/v1/views/{view['id']}/preferences", json=prefs, headers=api["viewer"]
    )
    assert put.status_code == 200 and put.json()["density"] == "compact"
    assert test_client.get(
        f"/v1/views/{view['id']}/preferences", headers=api["viewer"]
    ).json()["collapsed_groups"] == ["done"]
    assert (
        test_client.get(
            f"/v1/views/{view['id']}/preferences", headers=api["manager"]
        ).json()["collapsed_groups"]
        == []
    )
    assert (
        test_client.put(
            f"/v1/views/{view['id']}/preferences",
            json={"theme": "x"},
            headers=api["viewer"],
        ).status_code
        == 422
    )
    sqlite_session.expire_all()
    assert sqlite_session.get(SavedView, view["id"]).updated_by is None


def test_a_view_settings_come_from_allowlists(test_client, api):
    url = f"/v1/companies/{api['company'].id}/views"
    for body in (
        {
            "name": "x",
            "filters": {"and": [{"field": "title", "op": "eq", "value": "a"}]},
        },
        {"name": "x", "group_by": "title"},
        {"name": "x", "order_by": "-password"},
        {"name": "x", "layout": "gantt"},
        {"name": "x", "access": "public"},
        {"name": "x", "visible_columns": ["title", "comments"]},
        {"name": ""},
    ):
        response = test_client.post(url, json=body, headers=api["manager"])
        assert response.status_code == 422, body
        assert response.json()["detail"]["field"]


def test_a_stored_filter_no_longer_allowed_is_reported_invalid(
    test_client, sqlite_session, api
):
    company = api["company"]
    stale = SavedView(
        company_id=company.id,
        name="Old",
        filters={"and": [{"field": "estimate", "op": "eq", "value": 3}]},
        access="company",
        owner_name="someone",
    )
    sqlite_session.add(stale)
    sqlite_session.commit()
    listed = test_client.get(f"/v1/companies/{company.id}/views", headers=api["viewer"])
    (view,) = listed.json()["views"]
    assert view["valid"] is False and view["invalid_reason"]["field"] == "estimate"
    through = test_client.get(
        "/v1/tasks",
        params={"company_id": company.id, "view_id": stale.id},
        headers=api["viewer"],
    )
    assert through.status_code == 422 and "tasks" not in through.json()
