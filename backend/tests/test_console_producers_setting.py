"""Console producers, the company's setting.

A Manager names a CLI per role (refused without the Git workflow, for an
unknown role or CLI; accepted with a warning on the host sandbox or with the
network off) and the provider's keys it may see; a Viewer reads; outsiders
are refused.

Console producers: a terminal coding agent produces a role's task, and the
delivery is what the daemon observes (``services/producers/console_producer.py``,
``services/producers/console_clis.py``, ``vscode-daemon/console_run.py``).
Real git in ``tmp_path`` and the real daemon, with a fake CLI on ``PATH`` (as
``test_console_assistants`` and the e2e's ``e2e-copilot`` do); the shared
fixtures are in ``console_producer_helpers``.
"""

import secrets

import console_producer_helpers
from config import settings
from console_producer_helpers import _member
from models import AssistantRuntimeState, Company

# The shared fixtures.
enforcement = console_producer_helpers.enforcement


def test_a_manager_names_a_console_producer_and_a_viewer_reads_it(
    sqlite_session, test_client, enforcement, monkeypatch
):
    monkeypatch.setattr(settings, "DELIVERY_SANDBOX_NETWORK", False)
    AssistantRuntimeState.__table__.create(sqlite_session.get_bind(), checkfirst=True)
    company = Company(
        company_name=f"Set {secrets.token_hex(3)}",
        git_workflow=False,
        delivery_sandbox="host",
    )
    sqlite_session.add(company)
    sqlite_session.commit()
    manager = _member(sqlite_session, company.id, "Manager")
    viewer = _member(sqlite_session, company.id, "Viewer")
    outsider = _member(sqlite_session, None, "Owner")
    url = f"/v1/companies/{company.id}/console_producers"

    read = test_client.get(url, headers=viewer)
    assert read.status_code == 200
    body = read.json()
    assert body["producers"] == {} and body["git_workflow"] is False
    assert [c["id"] for c in body["clis"]] == ["claude", "codex", "gemini", "cursor"]
    assert "Developer" in body["roles"]
    assert test_client.get(url, headers=outsider).status_code == 403
    assert test_client.get(url).status_code == 401
    choice = {"producers": {"Developer": "claude"}}
    assert test_client.put(url, json=choice, headers=viewer).status_code == 403
    assert test_client.put(url, json=choice, headers=outsider).status_code == 403
    # Without the Git workflow: refused, the same way edits never touch the checkout.
    refused = test_client.put(url, json=choice, headers=manager)
    assert refused.status_code == 422 and "Git workflow" in refused.json()["detail"]

    sqlite_session.refresh(company)
    company.git_workflow = True
    sqlite_session.add(company)
    sqlite_session.commit()
    saved = test_client.put(url, json=choice, headers=manager)
    assert saved.status_code == 200 and saved.json()["producers"] == {
        "Developer": "claude"
    }
    # Accepted on the host sandbox, with the warning that no CLI runs there.
    assert saved.json()["sandbox"]["mode"] == "host"
    assert "container or bwrap" in saved.json()["warnings"][0]
    sqlite_session.refresh(company)
    company.delivery_sandbox = "container"
    sqlite_session.add(company)
    sqlite_session.commit()
    assert "network on" in test_client.get(url, headers=viewer).json()["warnings"][0]
    monkeypatch.setattr(settings, "DELIVERY_SANDBOX_NETWORK", True)
    assert test_client.get(url, headers=viewer).json()["warnings"] == []
    assert test_client.get(url, headers=viewer).json()["producers"] == {
        "Developer": "claude"
    }
    for bad in ({"Developer": "copilot"}, {"PO": "claude"}, {"Developer": "rm -rf"}):
        assert (
            test_client.put(url, json={"producers": bad}, headers=manager).status_code
            == 422
        )
    cleared = test_client.put(
        url, json={"producers": {"Developer": ""}}, headers=manager
    )
    assert cleared.json()["producers"] == {}
    # Another company's Manager cannot touch it.
    other = Company(company_name=f"Other {secrets.token_hex(3)}", git_workflow=True)
    sqlite_session.add(other)
    sqlite_session.commit()
    stranger = _member(sqlite_session, other.id, "Manager")
    assert test_client.put(url, json=choice, headers=stranger).status_code == 403


def test_a_manager_sets_the_console_env(
    sqlite_session, test_client, enforcement, monkeypatch
):
    AssistantRuntimeState.__table__.create(sqlite_session.get_bind(), checkfirst=True)
    company = Company(company_name=f"Env {secrets.token_hex(3)}", git_workflow=True)
    sqlite_session.add(company)
    sqlite_session.commit()
    manager = _member(sqlite_session, company.id, "Manager")
    url = f"/v1/companies/{company.id}/console_producers"
    body = {"producers": {"Developer": "claude"}, "env": ["OPENAI_API_KEY", ""]}
    saved = test_client.put(url, json=body, headers=manager)
    assert saved.status_code == 200 and saved.json()["env"] == ["OPENAI_API_KEY"]
    # Omitted, it is kept; a bad name is refused.
    kept = test_client.put(url, json={"producers": {}}, headers=manager)
    assert kept.json()["env"] == ["OPENAI_API_KEY"]
    bad = test_client.put(url, json={"producers": {}, "env": ["A B"]}, headers=manager)
    assert bad.status_code == 422
    sqlite_session.refresh(company)
    assert company.sandbox_env is None
