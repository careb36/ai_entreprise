"""The jobs of ``land_branch``, ``push_main`` and ``close_issue``
(``services/management/owner_git_runner.py``) and the GitHub adapter's issue
calls they use, against a faked daemon and a faked GitHub: no land.sh, no
push and no request to GitHub.

The land sends ``LAND_OWNER`` only for a protected land whose start passed
the step-up, and records the daemon's observations apart from land.sh's
record; the push records the range read back; the close posts exactly the
confirmed text. Start and finish are rows of the company's hash chain.
"""

import asyncio
import secrets

import audit_signing
import httpx
import pytest
from audit_chain import verify_chain
from models import AuditLog, Company, User
from services.management import owner_actions, owner_git_runner
from services.management import owner_git_actions as git
from services.producers import github_adapter
from services.workspace import workspace_daemon
from sqlmodel import Session, select

MAIN, HEAD, NEW = "a" * 40, "b" * 40, "f" * 40


@pytest.fixture(autouse=True)
def _audit(monkeypatch, tmp_path):
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "audit"))
    key = secrets.token_hex(16)
    monkeypatch.setattr(audit_signing, "_configured_key", lambda: key)


class FakeGitHub:
    """The adapter's issue functions, recording what they were sent."""

    GitHubApiError = github_adapter.GitHubApiError
    GitHubRateLimited = github_adapter.GitHubRateLimited

    def __init__(self, close_fails=False):
        self.sent, self.closed, self.close_fails = [], [], close_fails

    def comment_on_issue(self, owner, repo, number, body, token=None):
        self.sent.append((owner, repo, number, body, token))
        return {"id": 5, "url": f"https://github.com/{owner}/{repo}/issues/{number}#c5", "body": body}

    def close_issue(self, owner, repo, number, token=None):
        if self.close_fails:
            raise github_adapter.GitHubApiError("GitHub answered 403", 403)
        self.closed.append((owner, repo, number))
        return {"url": f"https://github.com/{owner}/{repo}/issues/{number}", "state": "closed"}


def _started(session, name, params, facts):
    company = Company(company_name=f"Jobs {secrets.token_hex(3)}")
    user = User(email=f"o-{secrets.token_hex(4)}@example.com", full_name="O", password_hash="x")
    session.add(company)
    session.add(user)
    session.commit()
    spec = owner_actions.action(name)
    model = spec.params.model_validate(params)
    digest = owner_actions.preview(spec, model, facts)["confirmation"]
    return owner_actions.start(session, company.id, user.id, spec, model, digest, facts=facts)


def _job(session, run, facts, daemon, github=None, land_owner=False):
    engine = session.get_bind()
    return owner_git_runner.GitJob(
        run_id=run.id, action=run.action, params=dict(run.params), facts=facts,
        land_owner=land_owner, session_factory=lambda: Session(engine),
        ctx=git.Context(daemon=daemon, github=github or FakeGitHub(), token="t"),
    )


def _land_facts(needs_owner):
    return {"branch": "feat/x", "head": HEAD, "main": MAIN, "already_in_main": False,
            "commits": [], "commit_count": 1, "commits_truncated": False, "diffstat": [],
            "files_truncated": False, "protected": [], "test_map_read": True,
            "needs_owner": needs_owner}


def _rows(session, run_id):
    return session.exec(select(AuditLog).where(
        AuditLog.interaction_id == f"owner_action:{run_id}").order_by(AuditLog.id)).all()


@pytest.mark.parametrize("needs_owner,stepped_up,sent", [
    (True, True, True), (True, False, False), (False, True, False)])
def test_land_owner_is_sent_only_for_a_protected_land_with_the_step_up(sqlite_session, needs_owner, stepped_up, sent):
    facts = _land_facts(needs_owner)
    run = _started(sqlite_session, git.LAND, {"branch": "feat/x"}, facts)
    asked = []

    async def daemon(subject, payload, timeout):
        asked.append((subject, payload))
        return {"ok": True, "branch": "feat/x", "head": HEAD, "land_owner": payload["land_owner"],
                "exit_code": 3, "outcome": "refused", "landed": False, "main_before": MAIN,
                "main_after": MAIN, "record": None, "output_tail": "land: refused"}

    done = asyncio.run(owner_git_runner.run(_job(sqlite_session, run, facts, daemon, land_owner=stepped_up)))
    ((subject, payload),) = asked
    assert subject == "workspace.self_host.land"
    assert payload == {"branch": "feat/x", "expected_head": HEAD, "land_owner": sent}
    assert (done.status, done.reason, done.exit_status) == ("failed", "refused", 3)


def test_a_land_records_main_and_land_sh_s_record_apart(sqlite_session):
    facts = _land_facts(False)
    run = _started(sqlite_session, git.LAND, {"branch": "feat/x"}, facts)
    record = {"nature": "claim", "status": "passed", "exit_code": 0, "commit": NEW,
              "command": "scripts/check.sh full", "duration_seconds": 900.0,
              "steps": [{"name": "backend", "status": "ok", "seconds": 300.0}]}

    async def daemon(subject, payload, timeout):
        return {"ok": True, "branch": "feat/x", "head": HEAD, "land_owner": False, "exit_code": 0,
                "outcome": "landed", "landed": True, "main_before": MAIN, "main_after": NEW,
                "record": record, "output_tail": "land: main is now ffffffffffff"}

    done = asyncio.run(owner_git_runner.run(_job(sqlite_session, run, facts, daemon)))
    o = done.observations
    assert done.status == "succeeded"
    assert (o["main_before"], o["main_after"], o["landed"]) == (MAIN, NEW, True)
    assert o["verification_record"] == record and o["verification_record"]["nature"] == "claim"
    assert o["check_summary"]["status"] == "passed" and o["check_summary"]["steps"][0]["name"] == "backend"
    assert o["confirmed"]["head"] == HEAD
    rows = _rows(sqlite_session, run.id)
    assert [r.action for r in rows] == ["owner_action_started", "owner_action_finished"]
    assert rows[-1].result["status"] == "succeeded" and rows[-1].agent_id.startswith("human:")
    assert verify_chain(sqlite_session, run.company_id)["status"] == "intact"


def _push_facts():
    return {"main": NEW, "origin_main": MAIN, "fast_forward": True, "up_to_date": False,
            "has_record": True, "notes": True, "commits": [], "commit_count": 1,
            "commits_truncated": False, "remote_url": "https://github.com/acme/fab.git", "public": True}


def test_a_push_records_the_range_read_back(sqlite_session):
    facts = _push_facts()
    run = _started(sqlite_session, git.PUSH, {}, facts)

    async def daemon(subject, payload, timeout):
        assert (subject, payload) == ("workspace.self_host.push", {"main": NEW, "origin_main": MAIN})
        return {"ok": True, "before": MAIN, "after": NEW, "main": NEW, "pushed": True,
                "notes_ref": "refs/notes/verification", "refspecs": [f"{NEW}:refs/heads/main"],
                "remote_url": "https://github.com/acme/fab.git"}

    done = asyncio.run(owner_git_runner.run(_job(sqlite_session, run, facts, daemon)))
    assert done.status == "succeeded"
    assert done.observations["pushed_range"] == {"from": MAIN, "to": NEW}


def test_a_refused_push_finishes_failed_with_the_daemon_s_reason(sqlite_session):
    facts = _push_facts()
    run = _started(sqlite_session, git.PUSH, {}, facts)

    async def daemon(subject, payload, timeout):
        raise workspace_daemon.DaemonError(409, "origin/main is not an ancestor of main: not a fast-forward")

    done = asyncio.run(owner_git_runner.run(_job(sqlite_session, run, facts, daemon)))
    assert done.status == "failed" and "not a fast-forward" in done.reason


def _close_facts():
    return {"repository": "acme/fab", "issue": {"number": 9, "title": "Broken",
            "url": "https://github.com/acme/fab/issues/9"}, "fix_commit": HEAD,
            "fix_subject": "Fix (#9)", "fix_on_origin": True, "origin_main": MAIN,
            "fetched": True, "candidates": []}


def _no_daemon(subject, payload, timeout):
    raise AssertionError("close_issue asks the daemon nothing in its job")


def test_the_close_posts_exactly_the_confirmed_text(sqlite_session):
    text = "Fixed by bbbb.\n\n  Two spaces, a tab\there, and ñ — kept as written.  "
    facts = _close_facts()
    run = _started(sqlite_session, git.CLOSE,
                   {"issue_number": 9, "fix_commit": HEAD, "comment": text}, facts)
    github = FakeGitHub()
    done = asyncio.run(owner_git_runner.run(_job(sqlite_session, run, facts, _no_daemon, github)))
    assert github.sent == [("acme", "fab", 9, text, "t")]
    assert github.closed == [("acme", "fab", 9)]
    o = done.observations
    assert done.status == "succeeded" and o["closed"] is True
    assert o["comment_sent"] == text and o["comment_stored_matches"] is True
    assert o["issue_url"] == "https://github.com/acme/fab/issues/9" and o["comment_url"].endswith("#c5")


def test_a_close_that_github_refuses_keeps_the_comment_it_posted(sqlite_session):
    facts = _close_facts()
    run = _started(sqlite_session, git.CLOSE, {"issue_number": 9, "comment": "Done."}, facts)
    done = asyncio.run(owner_git_runner.run(
        _job(sqlite_session, run, facts, _no_daemon, FakeGitHub(close_fails=True))))
    assert done.status == "failed" and done.reason.startswith("close_failed")
    assert done.observations["comment_url"] and done.observations["closed"] is False


def test_a_cancel_before_the_call_sends_nothing(sqlite_session):
    facts = _land_facts(False)
    run = _started(sqlite_session, git.LAND, {"branch": "feat/x"}, facts)
    owner_actions.request_cancel(sqlite_session, run, run.started_by_user_id)

    async def daemon(subject, payload, timeout):
        raise AssertionError("cancelled: the daemon is not asked")

    done = asyncio.run(owner_git_runner.run(_job(sqlite_session, run, facts, daemon)))
    assert done.status == "cancelled"
    assert [r.action for r in _rows(sqlite_session, run.id)] == [
        "owner_action_started", "owner_action_cancel_requested", "owner_action_finished"]


# -- the adapter --------------------------------------------------------------------


def test_the_adapter_sends_the_body_as_given_and_names_its_errors(monkeypatch):
    sent = []

    def request(method, url, headers, json, timeout):
        sent.append((method, url, json, headers.get("Authorization")))
        return httpx.Response(201, json={"id": 1, "html_url": "u", "body": json.get("body")},
                              request=httpx.Request(method, url))

    monkeypatch.setattr(github_adapter.httpx, "request", request)
    out = github_adapter.comment_on_issue("acme", "fab", 9, "  exact\ntext ", token="t")
    assert out["body"] == "  exact\ntext "
    assert sent[0] == ("POST", "https://api.github.com/repos/acme/fab/issues/9/comments",
                       {"body": "  exact\ntext "}, "Bearer t")
    github_adapter.close_issue("acme", "fab", 9, token="t")
    assert sent[1][0] == "PATCH" and sent[1][2] == {"state": "closed", "state_reason": "completed"}

    def refused(method, url, headers, json, timeout):
        return httpx.Response(404, json={}, request=httpx.Request(method, url))

    monkeypatch.setattr(github_adapter.httpx, "request", refused)
    with pytest.raises(github_adapter.GitHubApiError) as caught:
        github_adapter.close_issue("acme", "fab", 9)
    assert caught.value.status == 404


@pytest.mark.parametrize("url,expected", [
    ("https://github.com/acme/fab.git", ("acme", "fab")),
    ("git@github.com:acme/fab.git", ("acme", "fab")),
    ("ssh://git@github.com/acme/fab", ("acme", "fab")),
    ("https://gitlab.com/acme/fab.git", None),
    ("https://github.com/acme/fab/../x", None),
    (None, None),
])
def test_the_repository_is_read_from_a_github_remote_only(url, expected):
    assert github_adapter.repository_of(url) == expected


def test_open_issues_leave_pull_requests_out(monkeypatch):
    def get(url, token, accept, params=None):
        return httpx.Response(200, json=[
            {"number": 9, "title": "Broken", "html_url": "i9"},
            {"number": 10, "title": "A PR", "html_url": "p10", "pull_request": {}},
        ], request=httpx.Request("GET", url))

    monkeypatch.setattr(github_adapter, "_get", get)
    assert github_adapter.list_open_issues("acme", "fab") == [
        {"number": 9, "title": "Broken", "url": "i9"}]
