"""The integration merge's guard on protected paths and the follow-up sprint
(``docs/design/rsi-loop.md``, section 3.3 layer 3, PR 3 of section 8;
REQ-13, REQ-21).

For a company an Owner marked as hosting the factory
(``companies.protected_paths_enabled``), the local merge of a sprint's
integration reads the integration's diff with real git (the daemon's
``workspace.git.diff``, here against a repository in ``tmp_path``) and refuses
a diff that touches a protected path unless the integration's approving
review, by a signed-in Owner at that commit and on that diff, names every
matched path in ``protected_paths_ack``. The refusal is audited, kept on the
integration and seen by the Manager agent's inbox; the model-planned
follow-up sprint does not run for such a company. The refusals come first,
then what is allowed.
"""

from __future__ import annotations

from unittest.mock import patch

import board_moves_helpers
import pytest
import test_local_merge
from board_moves_helpers import _company as _plain_company
from board_moves_helpers import _item, _sprint
from models import AuditLog, Company, Sprint
from models.integration import SprintIntegration
from models.integration_review import IntegrationReview
from models.manager import ManagerDecision
from services.loop.worker import close as worker_close
from services.management import (
    manager_actions_extra,
    manager_inbox_extra,
    manager_observe,
    manager_policy,
    manager_rules,
)
from services.platform import api_tokens
from services.release import protected_merge
from sqlmodel import select
from test_git_workflow import _company, enforcement, git  # noqa: F401
from test_integration_review import _person, _ReviewingDaemon

GRADER = "bench/specs/01-cli-wordcount/acceptance/test_x.py"
RATCHET = "scripts/check_file_sizes.py"
db = board_moves_helpers.db
repo = test_local_merge.repo


@pytest.fixture(autouse=True)
def _audit_path(monkeypatch, tmp_path):
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "audit"))


def _host(session, company, on=True):
    company = session.get(Company, company.id)
    company.protected_paths_enabled = on
    session.add(company)
    session.commit()
    return company


def _commit_on(repo, branch, files):
    git(repo, "checkout", "-q", branch)
    for path, content in files.items():
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        git(repo, "add", path)
    git(repo, "commit", "-q", "-m", "integrated")
    commit = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-q", "main")
    return commit


def _integration(session, company, repo, files, declared=None):
    sprint = Sprint(company_id=company.id, sprint_name="Local", status="completed")
    session.add(sprint)
    session.commit()
    branch = f"fabrique/sprint-{sprint.id}"
    git(repo, "branch", branch, "main")
    commit = _commit_on(repo, branch, files)
    row = SprintIntegration(
        company_id=company.id,
        sprint_id=sprint.id,
        trigger="manual",
        requested_by="human:Ana",
        status="tests_passed",
        branch=branch,
        base="main",
        repository=str(repo),
        commit=commit,
        # What the tasks declared: a claim the guard never reads.
        tasks=[{"id": 1, "files": declared or ["s.py"]}],
    )
    session.add(row)
    session.commit()
    return sprint, row


def _world(session, client, repo, monkeypatch, files, on=True):
    company = _company(session)
    _host(session, company, on)
    sprint, row = _integration(session, company, repo, files)
    daemon = _ReviewingDaemon()
    monkeypatch.setattr(client.app.state, "nats_client", daemon)
    people = {r: _person(session, company.id, r) for r in ("Manager", "Owner")}
    return company, sprint, row, daemon, people


def _audits(session, company_id, action):
    return session.exec(
        select(AuditLog).where(
            AuditLog.company_id == company_id, AuditLog.action == action
        )
    ).all()


def _approve(client, sprint, headers, ack=None):
    body = {"event": "approve", "body": "Read it"}
    if ack is not None:
        body["protected_paths_ack"] = ack
    return client.post(
        f"/v1/sprints/{sprint.id}/integration/reviews", json=body, headers=headers
    )


def _merge(client, sprint, headers):
    return client.post(f"/v1/sprints/{sprint.id}/integration/merge", headers=headers)


BOTH = {"s.py": "s = 1\n", GRADER: "def test_x():\n    assert True\n"}


# -- Refusals -------------------------------------------------------------------


@pytest.mark.usefixtures("enforcement")
def test_no_ack_refuses_the_merge_naming_the_paths_and_audits_it(
    sqlite_session,
    test_client,
    repo,
    monkeypatch,
):
    company, sprint, row, _, people = _world(
        sqlite_session, test_client, repo, monkeypatch, BOTH
    )
    _, manager = people["Manager"]
    before = git(repo, "rev-parse", "main")

    refused = _merge(test_client, sprint, manager)

    assert refused.status_code == 409, refused.text
    assert (
        GRADER in refused.json()["detail"]
        and "s.py" not in (refused.json()["detail"].split("(")[1].split(")")[0])
    )
    assert git(repo, "rev-parse", "main") == before
    audit = _audits(sqlite_session, company.id, protected_merge.AUDIT_REFUSED)
    assert len(audit) == 1 and audit[0].result["matched"] == [GRADER]
    sqlite_session.refresh(row)
    kept = row.details["protected_paths"]
    assert (kept["matched"], kept["acknowledged"], kept["commit"]) == (
        [GRADER],
        False,
        row.commit,
    )
    # An approval that names nothing is no acknowledgement.
    assert _approve(test_client, sprint, people["Owner"][1]).status_code == 201
    again = _merge(test_client, sprint, manager)
    assert again.status_code == 409 and "acknowledges none" in again.json()["detail"]
    assert git(repo, "rev-parse", "main") == before


@pytest.mark.usefixtures("enforcement")
def test_an_ack_at_an_older_commit_does_not_cover_the_new_one(
    sqlite_session,
    test_client,
    repo,
    monkeypatch,
):
    _, sprint, row, _, people = _world(
        sqlite_session, test_client, repo, monkeypatch, BOTH
    )
    acked = _approve(test_client, sprint, people["Owner"][1], [GRADER])
    assert acked.status_code == 201, acked.text
    assert acked.json()["protected_paths_ack"] == [GRADER]
    old = row.commit
    # The integration moves on: same path, other bytes, a new head.
    row.commit = _commit_on(repo, row.branch, {GRADER: "def test_x():\n    pass\n"})
    sqlite_session.add(row)
    sqlite_session.commit()

    refused = _merge(test_client, sprint, people["Manager"][1])

    assert refused.status_code == 409, refused.text
    assert f"was of commit {old[:12]}" in refused.json()["detail"]


@pytest.mark.usefixtures("enforcement")
def test_an_ack_missing_a_path_refuses_the_merge(
    sqlite_session,
    test_client,
    repo,
    monkeypatch,
):
    files = {**BOTH, RATCHET: "# loosened\n"}
    _, sprint, _, _, people = _world(
        sqlite_session, test_client, repo, monkeypatch, files
    )
    assert _approve(test_client, sprint, people["Owner"][1], [GRADER]).status_code == (
        201
    )

    refused = _merge(test_client, sprint, people["Manager"][1])

    assert refused.status_code == 409
    assert f"does not name {RATCHET}" in refused.json()["detail"]


@pytest.mark.usefixtures("enforcement")
def test_a_manager_or_a_token_cannot_record_an_ack(
    sqlite_session,
    test_client,
    repo,
    monkeypatch,
):
    company, sprint, row, _, people = _world(
        sqlite_session, test_client, repo, monkeypatch, BOTH
    )
    owner, _ = people["Owner"]
    _, raw = api_tokens.create_token(
        sqlite_session, company.id, "ci", owner.email, [], role="Manager"
    )
    for headers in (people["Manager"][1], {"Authorization": f"Bearer {raw}"}):
        refused = _approve(test_client, sprint, headers, [GRADER])
        assert refused.status_code == 403, refused.text
    assert not sqlite_session.exec(
        select(IntegrationReview).where(IntegrationReview.integration_id == row.id)
    ).all()


@pytest.mark.parametrize("author_user_id", [None, -7])
@pytest.mark.usefixtures("enforcement")
def test_an_ack_on_record_not_by_a_signed_in_owner_refuses_the_merge(
    sqlite_session,
    test_client,
    repo,
    monkeypatch,
    author_user_id,
):
    """A review the Manager agent (no account) or a token (a negative id) holds."""
    _, sprint, row, _, people = _world(
        sqlite_session, test_client, repo, monkeypatch, BOTH
    )
    diff = test_client.get(
        f"/v1/sprints/{sprint.id}/integration/diff", headers=people["Manager"][1]
    ).json()
    sqlite_session.add(
        IntegrationReview(
            company_id=row.company_id,
            sprint_id=row.sprint_id,
            integration_id=row.id,
            event="approve",
            outcome="approved",
            commit=row.commit,
            diff_sha256=diff["diff_sha256"],
            protected_paths_ack=[GRADER],
            author_name="Manager agent",
            author_user_id=author_user_id,
        )
    )
    sqlite_session.commit()

    refused = _merge(test_client, sprint, people["Manager"][1])

    assert refused.status_code == 409
    assert "not recorded by a signed-in Owner" in refused.json()["detail"]


@pytest.mark.usefixtures("enforcement")
def test_declared_paths_do_not_hide_an_observed_match(
    sqlite_session,
    test_client,
    repo,
    monkeypatch,
):
    """The tasks recorded only ``s.py``; git's diff shows the grader."""
    _, sprint, row, daemon, people = _world(
        sqlite_session, test_client, repo, monkeypatch, BOTH
    )
    assert row.tasks == [{"id": 1, "files": ["s.py"]}]

    refused = _merge(test_client, sprint, people["Manager"][1])

    assert refused.status_code == 409 and GRADER in refused.json()["detail"]
    assert [s for s, _ in daemon.requests] == ["workspace.git.diff"]
    # Nor can an Owner acknowledge a path the diff does not show.
    elsewhere = _approve(test_client, sprint, people["Owner"][1], ["bench/other.py"])
    assert elsewhere.status_code == 422


def test_a_diff_cut_short_or_of_another_commit_is_refused(sqlite_session, repo):
    company = _host(sqlite_session, _company(sqlite_session))
    _, row = _integration(sqlite_session, company, repo, {"s.py": "s = 1\n"})
    cut = {"commit": row.commit, "files": [], "truncated": True, "diff_sha256": "0"}
    with pytest.raises(protected_merge.ProtectedMergeRefused, match="cut short"):
        protected_merge.guard(sqlite_session, row, cut, None)
    with pytest.raises(protected_merge.ProtectedMergeRefused, match="not read"):
        protected_merge.guard(sqlite_session, row, None, None)
    other = {**cut, "truncated": False, "commit": "1" * 40}
    with pytest.raises(protected_merge.ProtectedMergeRefused, match="not read"):
        protected_merge.guard(sqlite_session, row, other, None)


@pytest.mark.usefixtures("enforcement")
def test_the_manager_agent_is_refused_and_then_sees_the_refusal(
    sqlite_session,
    test_client,
    repo,
    monkeypatch,
):
    company, sprint, row, daemon, people = _world(
        sqlite_session, test_client, repo, monkeypatch, BOTH
    )

    def requester(subject, payload, timeout):
        import asyncio

        return asyncio.run(daemon.request(subject, payload, timeout))

    manager_observe.set_requester(requester)
    try:
        decision = ManagerDecision(
            company_id=company.id,
            item_kind="integration",
            item_id=row.id,
            item_state="x",
            category="merge_local",
        )
        with pytest.raises(manager_actions_extra.Failed, match=GRADER):
            manager_actions_extra._merge(sqlite_session, decision)
    finally:
        manager_observe.set_requester(None)
    sqlite_session.refresh(row)
    assert not (row.details or {}).get("local_merge")

    [item] = manager_inbox_extra._integration_items(sqlite_session, company)
    assert ":protected_awaiting_ack:" in item["state"]
    assert item["facts"]["protected_paths"] == [GRADER]
    assert GRADER in item["text"]
    why = manager_rules.rule_holds(
        "merge_local", item["facts"], manager_policy.Policy()
    )
    assert why is not None and GRADER in why and "Owner" in why

    # The Owner acknowledges: the item changes, and the rule lets it through.
    assert _approve(test_client, sprint, people["Owner"][1], [GRADER]).status_code == (
        201
    )
    sqlite_session.expire_all()
    [item] = manager_inbox_extra._integration_items(sqlite_session, company)
    assert ":protected_acked:" in item["state"]
    assert (
        manager_rules.rule_holds("merge_local", item["facts"], manager_policy.Policy())
        is None
    )


def test_the_follow_up_sprint_is_off_for_a_company_hosting_the_factory(db):
    company = _host(db, _plain_company(db, autonomy=3))
    sprint = _sprint(db, company, status="active")
    _item(db, sprint, status="done", comments=[], title="Done")

    with (
        patch("services.loop.worker.close.AUTO_SELF_IMPROVEMENT_ENABLED", True),
        patch(
            "services.loop.worker.close.plan_self_improvement_sprint",
            side_effect=AssertionError("no model-planned follow-up"),
        ),
    ):
        result = worker_close._complete_sprint_and_plan_follow_up(sprint.id)

    assert (result["completed"], result["auto_opened"]) == (True, False)
    assert result["reason"] == "protected_paths_enabled"
    [audit] = _audits(db, company.id, "follow_up_sprint_skipped")
    assert audit.result["reason"] == "protected_paths_enabled"
    assert (
        len(db.exec(select(Sprint).where(Sprint.company_id == company.id)).all()) == 1
    )


# -- What is allowed ------------------------------------------------------------


@pytest.mark.usefixtures("enforcement")
def test_without_the_flag_the_merge_reads_no_diff(
    sqlite_session,
    test_client,
    repo,
    monkeypatch,
):
    _, sprint, row, daemon, people = _world(
        sqlite_session, test_client, repo, monkeypatch, BOTH, on=False
    )
    merged = _merge(test_client, sprint, people["Manager"][1])
    assert merged.status_code == 201, merged.text
    assert [s for s, _ in daemon.requests] == ["workspace.git.merge"]
    assert merged.json()["details"]["local_merge"]["protected_paths"] == []
    assert git(repo, "rev-parse", "main") == row.commit


@pytest.mark.usefixtures("enforcement")
def test_with_the_flag_a_diff_with_no_protected_path_merges(
    sqlite_session,
    test_client,
    repo,
    monkeypatch,
):
    _, sprint, row, daemon, people = _world(
        sqlite_session, test_client, repo, monkeypatch, {"s.py": "s = 1\n"}
    )
    merged = _merge(test_client, sprint, people["Manager"][1])
    assert merged.status_code == 201, merged.text
    assert [s for s, _ in daemon.requests] == [
        "workspace.git.diff",
        "workspace.git.merge",
    ]
    details = merged.json()["details"]
    assert details["protected_paths"]["matched"] == []
    assert details["local_merge"]["protected_paths"] == []
    assert git(repo, "rev-parse", "main") == row.commit


@pytest.mark.usefixtures("enforcement")
def test_an_owners_ack_naming_every_path_merges_and_is_audited(
    sqlite_session,
    test_client,
    repo,
    monkeypatch,
):
    files = {**BOTH, RATCHET: "# changed\n"}
    company, sprint, row, _, people = _world(
        sqlite_session, test_client, repo, monkeypatch, files
    )
    owner, owner_h = people["Owner"]
    acked = _approve(test_client, sprint, owner_h, [RATCHET, "./" + GRADER])
    assert acked.status_code == 201, acked.text
    assert acked.json()["protected_paths_ack"] == [RATCHET, GRADER]
    [audit] = _audits(sqlite_session, company.id, "integration_review_approve")
    assert audit.result["protected_paths_ack"] == [RATCHET, GRADER]
    assert audit.details["user_id"] == owner.id

    merged = _merge(test_client, sprint, people["Manager"][1])

    assert merged.status_code == 201, merged.text
    local = merged.json()["details"]["local_merge"]
    assert sorted(local["protected_paths"]) == sorted([GRADER, RATCHET])
    assert local["review_id"] == acked.json()["id"]
    assert git(repo, "rev-parse", "main") == row.commit
    assert not _audits(sqlite_session, company.id, protected_merge.AUDIT_REFUSED)


def test_without_the_flag_the_follow_up_is_still_planned(db):
    company = _plain_company(db, autonomy=3)
    sprint = _sprint(db, company, status="active")
    _item(db, sprint, status="done", comments=[], title="Done")
    plan = {"sprint_name": "Next", "goal": "g", "tasks": [{"title": "t"}]}
    with (
        patch("services.loop.worker.close.AUTO_SELF_IMPROVEMENT_ENABLED", True),
        patch(
            "services.loop.worker.close.plan_self_improvement_sprint", return_value=plan
        ),
        patch(
            "services.loop.worker.close.build_self_improvement_objective",
            return_value="g",
        ),
    ):
        result = worker_close._complete_sprint_and_plan_follow_up(sprint.id)
    assert result["reason"] == "auto_follow_up_opened"
