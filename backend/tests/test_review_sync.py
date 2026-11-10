"""
Comments from the pull or merge request, brought back to the run.

The workspace daemon reads them with the person's ``gh`` or ``glab`` login
(``vscode-daemon/platform_reviews.py``); the backend keeps each one as a
``ReviewComment`` with its source and id there (``services/release/review_sync.py``).
These tests put a fake ``gh`` and ``glab`` on the PATH.
"""

import json
import shutil
import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "vscode-daemon"))
import platform_reviews
from models.agent_run import AgentRun
from models.review import ReviewComment, RunPublication
from services.run_review import feedback_text
from test_git_workflow import _company, _member, enforcement  # noqa: F401

PR_URL = "https://github.com/ana/app/pull/12"
MR_URL = "https://gitlab.com/team/app/-/merge_requests/4"


@pytest.fixture
def bin_dir(tmp_path, monkeypatch):
    """A PATH with the fakes and ``cat`` only: a real gh or glab on the machine is never reached."""
    path = tmp_path / "bin"
    path.mkdir()
    (path / "cat").symlink_to(shutil.which("cat"))
    monkeypatch.setenv("PATH", str(path))
    return path


def _fake(bin_dir, name, answers):
    """A CLI whose ``api`` call prints the answer for the endpoint it names."""
    cases = "\n".join(
        f"  *{endpoint}*) cat <<'JSON'\n{body}\nJSON\n  ;;"
        for endpoint, body in answers.items()
    )
    script = f'#!/bin/sh\ncase "$*" in\n{cases}\n  *) echo "unexpected: $*" >&2; exit 1 ;;\nesac\n'
    path = bin_dir / name
    path.write_text(script, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


GITHUB_ANSWERS = {
    # Two pages, as --paginate writes them.
    "pulls/12/comments": json.dumps(
        [
            {
                "id": 1,
                "path": "a.py",
                "line": 3,
                "side": "RIGHT",
                "body": "Name this better.",
                "user": {"login": "ana"},
                "html_url": f"{PR_URL}#discussion_r1",
            }
        ]
    )
    + json.dumps(
        [
            {
                "id": 2,
                "path": "a.py",
                "line": None,
                "original_line": 1,
                "side": "LEFT",
                "body": "Why removed?",
                "user": {"login": "leo"},
                "html_url": f"{PR_URL}#discussion_r2",
            }
        ]
    ),
    "pulls/12/reviews": json.dumps(
        [
            {
                "id": 10,
                "state": "CHANGES_REQUESTED",
                "body": "Needs tests.",
                "user": {"login": "ana"},
                "html_url": f"{PR_URL}#pullrequestreview-10",
            },
            {"id": 11, "state": "PENDING", "body": "draft", "user": {"login": "ana"}},
            {"id": 12, "state": "COMMENTED", "body": "", "user": {"login": "leo"}},
        ]
    ),
    "issues/12/comments": json.dumps(
        [
            {
                "id": 20,
                "body": "Thanks!",
                "user": {"login": "mia"},
                "html_url": f"{PR_URL}#issuecomment-20",
            }
        ]
    ),
}


# --- the daemon --------------------------------------------------------------


def test_github_comments_come_back_in_one_shape(bin_dir, tmp_path):
    _fake(bin_dir, "gh", GITHUB_ANSWERS)

    reply = platform_reviews.fetch(str(tmp_path), {"provider": "github", "url": PR_URL})

    assert reply["ok"] is True
    by_id = {c["external_id"]: c for c in reply["comments"]}
    assert set(by_id) == {
        "github:review_comment:1",
        "github:review_comment:2",
        "github:review:10",
        "github:issue_comment:20",
    }  # a pending review and an empty comment-only review are left out
    assert (
        by_id["github:review_comment:1"]["path"],
        by_id["github:review_comment:1"]["line"],
    ) == ("a.py", 3)
    old = by_id["github:review_comment:2"]
    assert (old["side"], old["line"], old["author"]) == (
        "old",
        1,
        "leo",
    )  # an outdated comment keeps its line
    review = by_id["github:review:10"]
    assert (review["kind"], review["state"], review["body"]) == (
        "review",
        "CHANGES_REQUESTED",
        "Needs tests.",
    )
    assert by_id["github:issue_comment:20"]["kind"] == "general"


def test_gitlab_notes_come_back_without_the_systems(bin_dir, tmp_path):
    discussions = [
        {
            "notes": [
                {
                    "id": 7,
                    "body": "Off by one.",
                    "author": {"username": "eva"},
                    "position": {"new_path": "b.py", "new_line": 9, "old_line": None},
                }
            ]
        },
        {
            "notes": [
                {
                    "id": 8,
                    "body": "added 1 commit",
                    "system": True,
                    "author": {"username": "eva"},
                },
                {"id": 9, "body": "Looks good overall.", "author": {"username": "eva"}},
            ]
        },
    ]
    _fake(
        bin_dir,
        "glab",
        {"projects/team%2Fapp/merge_requests/4/discussions": json.dumps(discussions)},
    )

    reply = platform_reviews.fetch(str(tmp_path), {"provider": "gitlab", "url": MR_URL})

    assert [
        (c["external_id"], c["kind"], c["path"], c["line"]) for c in reply["comments"]
    ] == [
        ("gitlab:note:7", "line", "b.py", 9),
        ("gitlab:note:9", "general", None, None),
    ]
    assert reply["comments"][0]["url"] == f"{MR_URL}#note_7"


def test_what_cannot_be_read_is_an_error_with_its_reason(bin_dir, tmp_path):
    with pytest.raises(platform_reviews.ReviewsError) as missing:
        platform_reviews.fetch(str(tmp_path), {"provider": "github", "url": PR_URL})
    assert (
        missing.value.status == 409 and "gh is not installed" in missing.value.message
    )
    with pytest.raises(platform_reviews.ReviewsError) as wrong:
        platform_reviews.fetch(
            str(tmp_path),
            {
                "provider": "github",
                "url": "https://github.com/ana/app/compare/main...x",
            },
        )
    assert wrong.value.status == 422
    _fake(bin_dir, "gh", {})  # every call fails
    with pytest.raises(platform_reviews.ReviewsError, match="gh api failed"):
        platform_reviews.fetch(str(tmp_path), {"provider": "github", "url": PR_URL})


def test_a_reply_too_big_for_one_message_keeps_what_fits_and_says_so():
    comments = [
        {"external_id": f"github:issue_comment:{i}", "body": "x" * 1000}
        for i in range(50)
    ]
    body = {"ok": True, "url": PR_URL, "comments": comments, "truncated": False}
    fitted = platform_reviews.fit(body, 10_000)
    assert len(json.dumps(fitted).encode()) <= 10_000
    assert 0 < len(fitted["comments"]) < 50 and fitted["truncated"] is True
    assert fitted["comments"] == comments[: len(fitted["comments"])]
    assert platform_reviews.fit(body, 0) is body
    assert platform_reviews.fit(body, 10**7) is body


# --- the backend -------------------------------------------------------------


class _Daemon:
    def __init__(self, reply):
        self.reply = reply
        self.requests = []

    async def request(self, subject, payload, timeout=5.0):
        self.requests.append((subject, payload))
        return self.reply

    async def close(self):
        return None

    async def drain(self):
        return None


def _published_run(session, company, *, how="gh", provider="github", url=PR_URL):
    run = AgentRun(
        company_id=company.id,
        external_ref=f"task-{how}-{provider}",
        producer="other-agent",
        artifact_text="x",
        artifact_digest="d",
        artifact_size=1,
    )
    session.add(run)
    session.commit()
    publication = RunPublication(
        company_id=company.id,
        agent_run_id=run.id,
        provider=provider,
        branch="fabrique/task-7",
        base="main",
        commit="a" * 40,
        url=url,
        how=how,
        published_by="Ana",
    )
    session.add(publication)
    session.commit()
    return run, publication


def _reply(body="Name this better."):
    return {
        "ok": True,
        "url": PR_URL,
        "truncated": False,
        "comments": [
            {
                "external_id": "github:review_comment:1",
                "kind": "line",
                "path": "a.py",
                "line": 3,
                "side": "new",
                "body": body,
                "author": "ana",
                "url": f"{PR_URL}#discussion_r1",
                "state": None,
            },
            {
                "external_id": "github:review:10",
                "kind": "review",
                "path": None,
                "line": None,
                "side": "new",
                "body": "",
                "author": "leo",
                "url": f"{PR_URL}#pullrequestreview-10",
                "state": "CHANGES_REQUESTED",
            },
            {
                "external_id": "gitlab:note:3",
                "kind": "general",
                "body": "not from this platform",
                "author": "x",
            },
        ],
    }


@pytest.mark.usefixtures("enforcement")
def test_a_manager_brings_the_comments_once_and_a_viewer_reads_them(
    sqlite_session, test_client, monkeypatch
):
    company = _company(sqlite_session)
    run, publication = _published_run(sqlite_session, company)
    manager = _member(sqlite_session, company.id, "Manager")
    viewer = _member(sqlite_session, company.id, "Viewer")
    daemon = _Daemon(_reply())
    monkeypatch.setattr(test_client.app.state, "nats_client", daemon)
    url = f"/v1/agent_runs/{run.id}/publications/{publication.id}/sync?company_id={company.id}"

    assert test_client.post(url, headers=viewer).status_code == 403
    first = test_client.post(url, headers=manager)

    assert first.status_code == 200, first.text
    assert (first.json()["added"], first.json()["updated"]) == (2, 0)
    subject, spec = daemon.requests[0]
    assert (subject, spec["provider"], spec["url"]) == (
        "workspace.git.reviews",
        "github",
        PR_URL,
    )
    comments = test_client.get(
        f"/v1/agent_runs/{run.id}/comments",
        params={"company_id": company.id},
        headers=viewer,
    ).json()["comments"]
    line, review = sorted(comments, key=lambda c: c["external_id"])[::-1]
    assert (line["source"], line["path"], line["line"], line["author_name"]) == (
        "github",
        "a.py",
        3,
        "ana",
    )
    assert (review["external_state"], review["body"], review["path"]) == (
        "CHANGES_REQUESTED",
        "changes requested",
        None,
    )

    again = test_client.post(url, headers=manager).json()
    assert (again["added"], again["updated"]) == (0, 0)
    daemon.reply = _reply("Name this `login_user`.")
    edited = test_client.post(url, headers=manager).json()
    assert (edited["added"], edited["updated"]) == (0, 1)


def test_a_comment_a_review_carried_keeps_what_it_said_then(
    sqlite_session, test_client, monkeypatch
):
    company = _company(sqlite_session)
    run, publication = _published_run(sqlite_session, company)
    daemon = _Daemon(_reply())
    monkeypatch.setattr(test_client.app.state, "nats_client", daemon)
    url = f"/v1/agent_runs/{run.id}/publications/{publication.id}/sync?company_id={company.id}"
    test_client.post(url)
    carried = test_client.post(
        f"/v1/agent_runs/{run.id}/reviews",
        json={"company_id": company.id, "event": "comment", "body": "Seen."},
    )
    assert carried.status_code == 201, carried.text

    daemon.reply = _reply("Changed later.")
    assert test_client.post(url).json()["updated"] == 0
    stored = sqlite_session.exec(
        ReviewComment.__table__.select().where(
            ReviewComment.external_id == "github:review_comment:1",
            ReviewComment.agent_run_id == run.id,
        )
    ).first()
    assert stored.body == "Name this better."


def test_the_feedback_says_who_wrote_it_and_where():
    comment = ReviewComment(
        company_id=1,
        agent_run_id=1,
        path="a.py",
        line=3,
        body="Name this better.",
        author_name="ana",
        source="github",
    )
    local = ReviewComment(
        company_id=1, agent_run_id=1, path=None, body="Add a test.", author_name="Caro"
    )

    text = feedback_text("Caro", None, [comment, local])

    assert "- a.py:3 (ana on GitHub): Name this better." in text
    assert "- the run: Add a test." in text


def test_a_publication_without_a_request_or_from_elsewhere_is_refused(
    sqlite_session, test_client, monkeypatch
):
    company = _company(sqlite_session)
    run, compare = _published_run(
        sqlite_session,
        company,
        how="compare",
        url="https://github.com/ana/app/compare/main...x",
    )
    monkeypatch.setattr(test_client.app.state, "nats_client", _Daemon(_reply()))

    refused = test_client.post(
        f"/v1/agent_runs/{run.id}/publications/{compare.id}/sync",
        params={"company_id": company.id},
    )
    assert refused.status_code == 409
    other = _company(sqlite_session)
    _other_run, other_publication = _published_run(sqlite_session, other)
    assert (
        test_client.post(
            f"/v1/agent_runs/{run.id}/publications/{other_publication.id}/sync",
            params={"company_id": company.id},
        ).status_code
        == 404
    )
