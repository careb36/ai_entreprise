"""Saved views of a sprint's tasks through the API (issue #535,
``services/tasks/saved_views.py``, ``routers/task_views.py``): REQ-3 the
views' access (a private view listed only by its author, a locked company
view refused to a Viewer), a view's filter and preferences kept apart, its
settings from allowlists and a stored filter no longer allowed reported
invalid. The filter itself is in ``test_task_view_filter.py``, its page
budget in ``test_task_view_budget.py``; the shared rows in
``task_views_helpers``.
"""

from __future__ import annotations

import secrets

import auth_tokens
import pytest
from config import settings
from models import (
    AuditLog,
    CompanyMember,
    SavedView,
    User,
)
from sqlmodel import select
from task_views_helpers import _company, _ids, _run, _sprint, _task

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
