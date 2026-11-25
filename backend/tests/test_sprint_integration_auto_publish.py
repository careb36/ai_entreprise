"""A closed sprint's passed integration opens its draft request on its own.

Real git in ``tmp_path``, a bare repository standing in for the remote, the
daemon's handlers behind a fake NATS client (``sprint_integration_helpers``).
The DevOps agent's account is a claim beside the platform's record; the
forge policy, a failed suite or an earlier publication keep the request
from opening, and the integration says why.
"""

import asyncio
import sys
from pathlib import Path

from models import Agent
from models.integration import SprintIntegration
from services.release import auto_publish

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "vscode-daemon"))
import sprint_integration_helpers
from sprint_integration_helpers import _Daemon, _two_done_tasks

# The shared fixtures.
published_repo = sprint_integration_helpers.published_repo
suite = sprint_integration_helpers.suite


def _integrated(sqlite_session, test_client, root, monkeypatch, forge="publish"):
    company, sprint, *_ = _two_done_tasks(sqlite_session, root)
    company.forge_policy = forge
    sqlite_session.add(company)
    sqlite_session.commit()
    daemon = _Daemon()
    monkeypatch.setattr(test_client.app.state, "nats_client", daemon)
    row = test_client.post(f"/v1/sprints/{sprint.id}/integrate").json()
    assert row["status"] == "tests_passed", row
    return company, sprint, daemon


def _publish(sqlite_session, daemon, sprint):
    note = asyncio.run(
        auto_publish.after_integration(daemon, sqlite_session.get_bind(), sprint.id)
    )
    sqlite_session.expire_all()
    return note


def _row(sqlite_session, sprint):
    return sqlite_session.query(SprintIntegration).filter_by(sprint_id=sprint.id).one()


def test_a_passed_integration_opens_its_draft_with_the_devops_account(
    sqlite_session, test_client, published_repo, suite, monkeypatch
):
    root, _, _ = published_repo
    company, sprint, daemon = _integrated(
        sqlite_session, test_client, root, monkeypatch
    )
    sqlite_session.add(Agent(company_id=company.id, name="Dana", role="DevOps"))
    sqlite_session.commit()
    asked = []

    def chat(self, messages, model, role, max_tokens=None, **_):
        asked.append((role, messages[-1]["content"]))
        return {"choices": [{"message": {"content": "### What changes\nAdds A."}}]}

    monkeypatch.setattr(auto_publish.LLMProxyClient, "chat", chat)
    note = _publish(sqlite_session, daemon, sprint)

    assert note["status"] == "published", note
    [(_, spec)] = [r for r in daemon.requests if r[0] == "workspace.git.publish"]
    body = spec["body"]
    observed, account = body.split(auto_publish.ACCOUNT_HEADING)
    assert "## Observed" in observed and "Adds A." in account
    assert body.rstrip().endswith(
        f"Fabrique-Integration: {_row(sqlite_session, sprint).id}"
    )
    # DevOps read the platform's record, nothing else.
    assert asked[0][0] == "DevOps" and "## Observed" in asked[0][1]
    row = _row(sqlite_session, sprint)
    assert row.publication["published_by"] == auto_publish.PUBLISHED_BY
    assert row.details["auto_publish"]["status"] == "published"
    # Once is enough: a second call leaves it as it is.
    assert _publish(sqlite_session, daemon, sprint)["status"] == "skipped"


def test_without_a_devops_agent_the_record_goes_alone(
    sqlite_session, test_client, published_repo, suite, monkeypatch
):
    root, _, _ = published_repo
    _, sprint, daemon = _integrated(sqlite_session, test_client, root, monkeypatch)
    assert _publish(sqlite_session, daemon, sprint)["status"] == "published"
    [(_, spec)] = [r for r in daemon.requests if r[0] == "workspace.git.publish"]
    assert auto_publish.ACCOUNT_HEADING not in spec["body"]


def test_a_company_that_does_not_publish_keeps_it_local_and_says_why(
    sqlite_session, test_client, published_repo, suite, monkeypatch
):
    root, _, _ = published_repo
    _, sprint, daemon = _integrated(
        sqlite_session, test_client, root, monkeypatch, forge="fetch"
    )
    note = _publish(sqlite_session, daemon, sprint)
    assert note["status"] == "not_published"
    assert not [r for r in daemon.requests if r[0] == "workspace.git.publish"]
    row = _row(sqlite_session, sprint)
    assert row.publication is None and row.details["auto_publish"] == note


def test_only_a_passed_suite_is_published(
    sqlite_session, test_client, published_repo, suite, monkeypatch
):
    root, _, _ = published_repo
    _, sprint, daemon = _integrated(sqlite_session, test_client, root, monkeypatch)
    row = _row(sqlite_session, sprint)
    row.status = "tests_failed"
    sqlite_session.add(row)
    sqlite_session.commit()
    note = _publish(sqlite_session, daemon, sprint)
    assert note["status"] == "not_published" and "tests_failed" in note["reason"]


def test_the_account_sits_before_the_trailer():
    body = "## Observed\n\nx\n\nFabrique-Sprint: 3\nFabrique-Integration: 9\n"
    out = auto_publish.with_account(body, "Risky.")
    assert out.index("Risky.") < out.index("Fabrique-Sprint: 3")
    assert auto_publish.with_account(body, None) == body
