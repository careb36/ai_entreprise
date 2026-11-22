"""Every change to an agent's configuration is a revision in the chained
audit log, and a Manager can put back what a revision replaced
(``services/loop/agent_config_history.py``, from Paperclip's agent
configuration revisions)."""

import pytest
import test_agent_access
from models import Agent
from models.audit import AuditLog
from services.loop import agent_config_history
from sqlmodel import select

company = test_agent_access.company
enforcement = test_agent_access.enforcement


@pytest.fixture(autouse=True)
def audit_file(monkeypatch, tmp_path):
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "audit"))


def _patch(test_client, company, headers, **body):
    return test_client.patch(
        f"/v1/agents/{company['agent']}", json=body, headers=headers
    )


def _revisions(test_client, company, headers):
    return test_client.get(
        f"/v1/agents/{company['agent']}/config-revisions", headers=headers
    )


def test_a_change_is_a_revision_with_before_after_and_who(
    test_client, sqlite_session, company
):
    changed = _patch(
        test_client,
        company,
        company["manager"],
        system_prompt="Write tests first.",
        model="",
        reason="TDD trial",
    )
    assert changed.status_code == 200, changed.text

    listed = _revisions(test_client, company, company["viewer"])
    assert listed.status_code == 200
    [rev] = listed.json()["revisions"]
    assert rev["changed"] == ["system_prompt"]
    assert rev["from"] == {"system_prompt": None}
    assert rev["to"] == {"system_prompt": "Write tests first."}
    assert rev["reason"] == "TDD trial" and rev["source"] == "edit"
    assert rev["user_id"] is not None
    row = sqlite_session.get(AuditLog, rev["revision_id"])
    assert row.action == agent_config_history.AUDIT_ACTION and row.chain_hash


def test_a_patch_that_changes_nothing_records_nothing(test_client, company):
    _patch(test_client, company, company["manager"], name="Dev Ana")
    assert (
        _revisions(test_client, company, company["manager"]).json()["revisions"] == []
    )


def test_a_manager_rolls_a_revision_back_and_the_rollback_is_a_revision(
    test_client, sqlite_session, company
):
    _patch(test_client, company, company["manager"], system_prompt="v1")
    _patch(
        test_client, company, company["manager"], system_prompt="v2", persona="terse"
    )
    newest = _revisions(test_client, company, company["manager"]).json()["revisions"][0]
    url = f"/v1/agents/{company['agent']}/config-revisions/{newest['revision_id']}/rollback"

    assert test_client.post(url, headers=company["viewer"]).status_code == 403
    assert test_client.post(url, headers=company["outsider"]).status_code == 403
    undone = test_client.post(
        url, json={"reason": "v2 blocked more"}, headers=company["manager"]
    )

    assert undone.status_code == 200, undone.text
    body = undone.json()
    assert (
        body["restored"] == ["persona", "system_prompt"] and body["changed_since"] == []
    )
    sqlite_session.expire_all()
    agent = sqlite_session.get(Agent, company["agent"])
    assert (agent.system_prompt, agent.persona) == ("v1", None)
    latest = _revisions(test_client, company, company["manager"]).json()["revisions"][0]
    assert latest["source"] == "rollback"
    assert latest["rolled_back_from"] == newest["revision_id"]
    assert latest["reason"] == "v2 blocked more"
    again = test_client.post(url, headers=company["manager"])
    assert again.status_code == 409


def test_a_rollback_says_which_fields_moved_since(test_client, company):
    _patch(test_client, company, company["manager"], system_prompt="v1")
    first = _revisions(test_client, company, company["manager"]).json()["revisions"][0]
    _patch(test_client, company, company["manager"], system_prompt="v2")
    url = f"/v1/agents/{company['agent']}/config-revisions/{first['revision_id']}/rollback"

    body = test_client.post(url, headers=company["manager"]).json()

    assert body["restored"] == ["system_prompt"] and body["changed_since"] == [
        "system_prompt"
    ]
    assert body["agent"]["system_prompt"] is None


def test_another_agents_or_companys_revision_is_not_found(
    test_client, sqlite_session, company
):
    other = Agent(company_id=company["id"], name="QA Bo", role="QA")
    sqlite_session.add(other)
    sqlite_session.commit()
    _patch(test_client, company, company["manager"], system_prompt="v1")
    rev = _revisions(test_client, company, company["manager"]).json()["revisions"][0]

    wrong = test_client.post(
        f"/v1/agents/{other.id}/config-revisions/{rev['revision_id']}/rollback",
        headers=company["manager"],
    )
    assert wrong.status_code == 404
    assert _revisions(test_client, company, company["outsider"]).status_code == 403
    rows = sqlite_session.exec(
        select(AuditLog).where(AuditLog.action == agent_config_history.AUDIT_ACTION)
    ).all()
    assert len(rows) == 1
