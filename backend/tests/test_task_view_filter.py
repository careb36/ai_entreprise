"""The filter of a saved view of a sprint's tasks (issue #535): its contract
(``services/contracts/task_filters.py``: the allowlist, the operators and
values per field, the bounds of the tree) and its evaluation from the task
projections (``services/tasks/task_filter.py``). REQ-1 is the allowlist and
REQ-2 agreement with the board, from the specification of #535
(``scripts/self_host/fixtures/issue-535-specification.json``). The shared
rows are in ``task_views_helpers``.
"""

from __future__ import annotations

import json

import pytest
from models import (
    BacklogItem,
    Sprint,
)
from services.contracts import task_filters
from services.evidence import risk
from services.tasks import task_filter
from sqlalchemy import inspect
from sqlmodel import select

from task_views_helpers import WAITS, _company, _ids, _run, _sprint, _task

RETRIES = [{"author": "System", "text": "Agent error: the call timed out."}]


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
