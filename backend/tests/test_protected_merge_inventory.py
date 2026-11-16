"""The merge guard's path inventory and the Manager agent's view of a changed
diff (``docs/design/rsi-loop.md``, REQ-13; review of PR #625).

The guard matches every path git's ``--name-status -z`` lists, which no
quoting can hide, and refuses a diff whose parsed files and inventory
disagree. An acknowledgement may name every path a full diff can hold. The
Manager agent's item changes whenever the diff or the approving review does,
and a review on a new diff replaces the check kept from an earlier attempt.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import test_local_merge
from models import Company
from models.integration_review import IntegrationReview
from pydantic import ValidationError
from schemas import IntegrationReviewCreate
from services.management import manager_inbox_extra
from services.release import integration_review, protected_merge
from test_git_workflow import _company, enforcement, git  # noqa: F401
from test_integration_review import _person, _ReviewingDaemon
from test_protected_merge import (
    BOTH,
    GRADER,
    RATCHET,
    _approve,
    _commit_on,
    _host,
    _integration,
    _merge,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "vscode-daemon"))
import git_diff

repo = test_local_merge.repo
TABBED = "notes/check\tsizes.py"


@pytest.fixture(autouse=True)
def _audit_path(monkeypatch, tmp_path):
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "audit"))


def _ratchet_on_main(repo):
    (repo / "scripts").mkdir(exist_ok=True)
    (repo / RATCHET).write_text("LIMIT = 600\n")
    git(repo, "add", RATCHET)
    git(repo, "commit", "-q", "-m", "ratchet")


def _renamed_world(session, client, repo, monkeypatch):
    """An integration whose only change renames the ratchet to a tabbed name."""
    _ratchet_on_main(repo)
    company = _host(session, _company(session))
    _, row = _integration(session, company, repo, {"s.py": "s = 1\n"})
    git(repo, "checkout", "-q", row.branch)
    git(repo, "reset", "-q", "--hard", "main")
    (repo / "notes").mkdir()
    git(repo, "mv", RATCHET, TABBED)
    git(repo, "commit", "-q", "-m", "rename")
    row.commit = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-q", "main")
    session.add(row)
    session.commit()
    monkeypatch.setattr(client.app.state, "nats_client", _ReviewingDaemon())
    return company, row


# -- Finding 1: a quoted header cannot hide a protected path --------------------


def test_the_daemon_lists_every_path_including_a_quoted_rename(repo):
    _ratchet_on_main(repo)
    git(repo, "checkout", "-q", "-b", "fabrique/sprint-1")
    (repo / "notes").mkdir()
    git(repo, "mv", RATCHET, TABBED)
    git(repo, "commit", "-q", "-m", "rename")
    commit = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-q", "main")

    reply = git_diff.integration_diff(str(repo), {"commit": commit, "base": "main"})

    assert sorted(reply["paths"]) == sorted([RATCHET, TABBED])
    assert reply["paths_truncated"] is False


@pytest.mark.usefixtures("enforcement")
def test_a_protected_file_renamed_to_a_quoted_name_is_not_merged(
    sqlite_session, test_client, repo, monkeypatch
):
    company, row = _renamed_world(sqlite_session, test_client, repo, monkeypatch)
    _, manager = _person(sqlite_session, company.id, "Manager")
    before = git(repo, "rev-parse", "main")

    refused = test_client.post(
        f"/v1/sprints/{row.sprint_id}/integration/merge", headers=manager
    )

    assert refused.status_code == 409, refused.text
    assert RATCHET in refused.json()["detail"]
    assert git(repo, "rev-parse", "main") == before


def _diff(row, files, paths, truncated=False):
    return {
        "commit": row.commit,
        "files": [{"path": p, "old_path": None} for p in files],
        "paths": paths,
        "paths_truncated": False,
        "truncated": truncated,
        "diff_sha256": "0" * 64,
    }


def test_an_inventory_missing_or_disagreeing_is_refused(sqlite_session, repo):
    company = _host(sqlite_session, _company(sqlite_session))
    _, row = _integration(sqlite_session, company, repo, {"s.py": "s = 1\n"})
    for diff, why in (
        (_diff(row, ["s.py"], None), "complete list"),
        (_diff(row, [], ["s.py", RATCHET]), "do not agree"),
        (_diff(row, ["s.py", "t.py"], ["s.py"]), "do not agree"),
        ({**_diff(row, ["s.py"], ["s.py"]), "paths_truncated": True}, "complete"),
    ):
        with pytest.raises(protected_merge.ProtectedMergeRefused, match=why):
            protected_merge.guard(sqlite_session, row, diff, None)
    assert (
        protected_merge.guard(
            sqlite_session, row, _diff(row, ["s.py"], ["./s.py"]), None
        )
        == []
    )


def test_an_older_daemon_without_an_inventory_reads_as_none(sqlite_session, repo):
    company = _company(sqlite_session)
    _, row = _integration(sqlite_session, company, repo, {"s.py": "s = 1\n"})
    reply = {"ok": True, "commit": row.commit, "merge_base": "1" * 40, "files": []}
    assert integration_review.observed(reply, row)["paths"] is None


# -- Finding 2: an acknowledgement as long as a full diff ------------------------


def test_an_ack_may_name_six_hundred_paths_and_no_more():
    paths = [f"bench/{i}.py" for i in range(600)]
    assert (
        len(
            IntegrationReviewCreate(
                event="approve", protected_paths_ack=paths
            ).protected_paths_ack
        )
        == 600
    )
    with pytest.raises(ValidationError):
        IntegrationReviewCreate(event="approve", protected_paths_ack=[*paths, "x"])


# -- Findings 3 and 4: the item follows the diff and the review ------------------


def _review(session, row, owner, digest, ack):
    review = IntegrationReview(
        company_id=row.company_id,
        sprint_id=row.sprint_id,
        integration_id=row.id,
        event="approve",
        outcome="approved",
        commit=row.commit,
        diff_sha256=digest,
        protected_paths_ack=ack,
        author_name="Owner",
        author_user_id=owner.id,
    )
    session.add(review)
    session.commit()
    return review


def _kept(session, row, digest, matched):
    row.details = {
        **(row.details or {}),
        "protected_paths": {
            "commit": row.commit,
            "diff_sha256": digest,
            "matched": matched,
            "truncated": False,
        },
    }
    session.add(row)
    session.commit()


@pytest.mark.usefixtures("enforcement")
def test_the_item_state_names_the_diff_and_the_approving_review(sqlite_session, repo):
    company = _host(sqlite_session, _company(sqlite_session))
    _, row = _integration(sqlite_session, company, repo, BOTH)
    owner, _ = _person(sqlite_session, company.id, "Owner")
    _kept(sqlite_session, row, "a" * 64, [GRADER])
    first = _review(sqlite_session, row, owner, "a" * 64, [GRADER])
    [item] = manager_inbox_extra._integration_items(sqlite_session, company)
    assert item["state"].endswith(f":protected_acked:{'a' * 12}:r{first.id}")

    # A refused attempt on a changed diff, then a new acknowledgement of it.
    _kept(sqlite_session, row, "b" * 64, [GRADER])
    second = _review(sqlite_session, row, owner, "b" * 64, [GRADER])
    [again] = manager_inbox_extra._integration_items(sqlite_session, company)
    assert again["state"].endswith(f":protected_acked:{'b' * 12}:r{second.id}")
    assert again["state"] != item["state"]


@pytest.mark.usefixtures("enforcement")
def test_a_review_on_a_new_diff_replaces_the_kept_check(
    sqlite_session, test_client, repo, monkeypatch
):
    """The base absorbs the ratchet change after a refused merge."""
    company = _host(sqlite_session, _company(sqlite_session))
    sprint, row = _integration(sqlite_session, company, repo, {RATCHET: "# a\n"})
    first = row.commit
    row.commit = _commit_on(repo, row.branch, {GRADER: "def test_x():\n    pass\n"})
    sqlite_session.add(row)
    sqlite_session.commit()
    monkeypatch.setattr(test_client.app.state, "nats_client", _ReviewingDaemon())
    _, manager = _person(sqlite_session, company.id, "Manager")
    _, owner = _person(sqlite_session, company.id, "Owner")

    assert _merge(test_client, sprint, manager).status_code == 409
    sqlite_session.refresh(row)
    assert sorted(row.details["protected_paths"]["matched"]) == sorted(
        [GRADER, RATCHET]
    )
    git(repo, "merge", "-q", "--ff-only", first)  # main takes the ratchet change

    acked = _approve(test_client, sprint, owner, [GRADER])
    assert acked.status_code == 201, acked.text

    sqlite_session.expire_all()
    company = sqlite_session.get(Company, company.id)
    [item] = manager_inbox_extra._integration_items(sqlite_session, company)
    assert item["facts"]["protected_paths"] == [GRADER]
    assert item["facts"]["protected_paths_acked"] is True
    assert ":protected_acked:" in item["state"]
    merged = _merge(test_client, sprint, manager)
    assert merged.status_code == 201, merged.text
