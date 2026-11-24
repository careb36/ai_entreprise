"""The owner actions on La Fabrique's own repository, through their routes
(``land_branch``, ``push_main``, ``close_issue``; ``docs/design/owner-actions.md``).

Only a signed-in Owner previews or starts them. The preview observes the
repository through the daemon and GitHub (both faked here: no land.sh, no
push, no GitHub call), and its digest covers what it observed, so a branch
that moved or another comment is a 409. A protected land needs the step-up,
a land waits for active sprints and self-hosting runs, a push that is not a
fast-forward is refused, and an issue whose fix is not on origin is closed
only with the explicit override. No job runs here
(``owner_git_runner.launch`` is replaced); the jobs have
``test_owner_git_runner.py``.
"""

import secrets

import audit_signing
import pytest
from models import AuditLog, Company, CompanyMember, OwnerActionRun, Sprint, User
from passwords import hash_password
from services.management import owner_actions, owner_git_runner
from services.platform import api_tokens, login_throttle
from services.producers import github_adapter
from services.workspace import workspace_daemon
from sqlmodel import select
from test_git_workflow import _member, enforcement  # noqa: F401

import auth_tokens

PASSWORD = "a-long-owner-password"
MAIN = "a" * 40
HEAD = "b" * 40
ORIGIN = "c" * 40
FIX = "d" * 40


@pytest.fixture(autouse=True)
def _audit(monkeypatch, tmp_path):
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "audit"))
    key = secrets.token_hex(16)
    monkeypatch.setattr(audit_signing, "_configured_key", lambda: key)
    login_throttle.reset()


@pytest.fixture
def world(monkeypatch):
    """The daemon's repository and GitHub, as data the test changes."""
    state = {
        "branches": [{"name": "feat/x", "head": HEAD, "subject": "Add x"}],
        "head": HEAD,
        "changed": ["frontend/src/x.ts"],
        "push": {"main": MAIN, "origin_main": ORIGIN, "fast_forward": True,
                 "up_to_date": False, "has_record": True, "notes": True,
                 "commits": [{"sha": MAIN, "subject": "Land feat/x into main"}],
                 "truncated": False, "remote_url": "https://github.com/acme/fab.git"},
        "fix_on_origin": True,
        "requests": [],
    }

    async def daemon_request(nats, subject, payload, timeout):
        state["requests"].append((subject, payload))
        view = payload.get("view")
        if view == "branches":
            return {"ok": True, "view": view, "main": MAIN, "branches": state["branches"]}
        if view == "land_preview":
            return {"ok": True, "view": view, "branch": payload["branch"],
                    "head": state["head"], "main": MAIN, "already_in_main": False,
                    "commits": [{"sha": state["head"], "subject": "Add x"}],
                    "truncated": False, "changed": state["changed"],
                    "diffstat": [{"path": p, "added": 1, "deleted": 0} for p in state["changed"]],
                    "files_truncated": False, "test_map": {"test_x.py": ["evidence"]}}
        if view == "push_preview":
            return {"ok": True, "view": view, **state["push"]}
        if view == "origin":
            return {"ok": True, "view": view, "remote_url": "git@github.com:acme/fab.git"}
        if view == "fix_commit":
            return {"ok": True, "view": view, "issue": payload["issue"],
                    "origin_main": ORIGIN, "fetched": True, "fetch_error": None,
                    "commits": [{"sha": FIX, "subject": "Fix it (#9)",
                                 "on_origin": state["fix_on_origin"]}],
                    "truncated": False, "remote_url": "git@github.com:acme/fab.git"}
        raise AssertionError(f"unexpected daemon request {subject} {payload}")

    def no_github_write(*args, **kwargs):
        raise AssertionError("the route must not write to GitHub")

    monkeypatch.setattr(workspace_daemon, "daemon_request", daemon_request)
    monkeypatch.setattr(github_adapter, "configured_token", lambda: "fake-token")
    monkeypatch.setattr(
        github_adapter,
        "list_open_issues",
        lambda owner, repo, token=None: [
            {"number": 9, "title": "Broken", "url": f"https://github.com/{owner}/{repo}/issues/9"}
        ],
    )
    monkeypatch.setattr(github_adapter, "comment_on_issue", no_github_write)
    monkeypatch.setattr(github_adapter, "close_issue", no_github_write)
    launched = []
    monkeypatch.setattr(
        owner_git_runner,
        "launch",
        lambda run, engine, ctx, facts, land_owner=False: launched.append(
            {"run": run.id, "facts": facts, "land_owner": land_owner, "params": run.params}
        ),
    )
    state["launched"] = launched
    return state


def _company(session):
    company = Company(company_name=f"Repo actions {secrets.token_hex(3)}")
    session.add(company)
    session.commit()
    session.refresh(company)
    return company


def _owner(session, company_id):
    """An Owner who signed in with a password (so the step-up asks for it)."""
    user = User(email=f"owner-{secrets.token_hex(4)}@example.com", full_name="O",
                password_hash=hash_password(PASSWORD))
    session.add(user)
    session.commit()
    session.add(CompanyMember(company_id=company_id, user_id=user.id, human_role="Owner"))
    session.commit()
    token = auth_tokens.create_access_token(user_id=user.id, email=user.email)
    return {"Authorization": f"Bearer {token}"}, user


def _preview(client, name, company_id, headers, params):
    response = client.post(f"/v1/owner_actions/{name}/preview",
                           json={"company_id": company_id, "params": params}, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def _start(client, name, company_id, headers, shown, password=None):
    body = {"company_id": company_id, "params": shown["params"],
            "confirmation": shown["confirmation"]}
    if password is not None:
        body["step_up_password"] = password
    return client.post(f"/v1/owner_actions/{name}/runs", json=body, headers=headers)


@pytest.mark.parametrize(
    "name,params",
    [("land_branch", {"branch": "feat/x"}), ("push_main", {}), ("close_issue", {"issue_number": 9})],
)
def test_a_manager_and_an_api_token_get_403(sqlite_session, test_client, enforcement, world, name, params):  # noqa: F811
    company = _company(sqlite_session)
    manager = _member(sqlite_session, company.id, "Manager")
    _, raw = api_tokens.create_token(sqlite_session, company.id, "ci", role="Manager")
    token = {"Authorization": f"Bearer {raw}"}
    for headers in (manager, token):
        for method, url, body in (
            ("post", f"/v1/owner_actions/{name}/preview", {"company_id": company.id, "params": params}),
            ("post", f"/v1/owner_actions/{name}/runs",
             {"company_id": company.id, "params": params, "confirmation": "x"}),
            ("get", f"/v1/owner_actions/{name}/options?company_id={company.id}", None),
        ):
            call = getattr(test_client, method)
            response = call(url, json=body, headers=headers) if body else call(url, headers=headers)
            assert response.status_code == 403, (name, url, response.text)
    assert world["launched"] == [] and world["requests"] == []


def test_the_catalogue_lists_the_three_actions_and_their_options(sqlite_session, test_client, enforcement, world):  # noqa: F811
    company = _company(sqlite_session)
    owner, _ = _owner(sqlite_session, company.id)
    names = [a["name"] for a in test_client.get(
        f"/v1/owner_actions?company_id={company.id}", headers=owner).json()["actions"]]
    assert names == ["self_host_container_run", "land_branch", "push_main", "close_issue"]
    branches = test_client.get(f"/v1/owner_actions/land_branch/options?company_id={company.id}",
                               headers=owner).json()
    assert [b["name"] for b in branches["branches"]] == ["feat/x"]
    issues = test_client.get(f"/v1/owner_actions/close_issue/options?company_id={company.id}",
                             headers=owner).json()
    assert issues["repository"] == "acme/fab" and issues["issues"][0]["number"] == 9


def test_a_branch_the_daemon_does_not_list_is_refused(sqlite_session, test_client, enforcement, world):  # noqa: F811
    company = _company(sqlite_session)
    owner, _ = _owner(sqlite_session, company.id)
    for branch, status in (("feat/other", 422), ("../x", 422), ("-x", 422), ("a b", 422)):
        response = test_client.post("/v1/owner_actions/land_branch/preview",
                                    json={"company_id": company.id, "params": {"branch": branch}},
                                    headers=owner)
        assert response.status_code == status, branch


def test_a_protected_land_needs_the_step_up(sqlite_session, test_client, enforcement, world):  # noqa: F811
    company = _company(sqlite_session)
    owner, user = _owner(sqlite_session, company.id)
    world["changed"] = ["docs/POSITIONING.md", "backend/tests/test_x.py", "frontend/a.ts"]
    shown = _preview(test_client, "land_branch", company.id, owner, {"branch": "feat/x"})
    assert shown["step_up"] is True and shown["facts"]["needs_owner"] is True
    assert [m["path"] for m in shown["facts"]["protected"]] == [
        "docs/POSITIONING.md", "backend/tests/test_x.py"]  # the list, and main's evidence tests
    assert "protected" in [e["key"] for e in shown["effect"]]

    missing = _start(test_client, "land_branch", company.id, owner, shown)
    assert missing.status_code == 403 and missing.json()["detail"]["code"] == "step_up_required"
    wrong = _start(test_client, "land_branch", company.id, owner, shown, password="nope")
    assert wrong.status_code == 403 and wrong.json()["detail"]["code"] == "step_up_failed"
    assert world["launched"] == []
    assert sqlite_session.exec(select(OwnerActionRun)).all() == []

    started = _start(test_client, "land_branch", company.id, owner, shown, password=PASSWORD)
    assert started.status_code == 202, started.text
    (launch,) = world["launched"]
    assert launch["land_owner"] is True and launch["facts"]["head"] == HEAD
    row = sqlite_session.exec(select(AuditLog).where(
        AuditLog.interaction_id == f"owner_action:{started.json()['id']}")).one()
    assert row.action == "owner_action_started" and row.result["step_up"] == "password"
    assert PASSWORD not in str(row.details) and PASSWORD not in str(row.result)
    assert started.json()["observations"]["confirmed"]["needs_owner"] is True


def test_an_unprotected_land_needs_no_step_up_and_never_sets_land_owner(sqlite_session, test_client, enforcement, world):  # noqa: F811
    company = _company(sqlite_session)
    owner, _ = _owner(sqlite_session, company.id)
    shown = _preview(test_client, "land_branch", company.id, owner, {"branch": "feat/x"})
    assert shown["step_up"] is False and shown["facts"]["protected"] == []
    started = _start(test_client, "land_branch", company.id, owner, shown)
    assert started.status_code == 202, started.text
    assert world["launched"][0]["land_owner"] is False


def test_a_land_is_refused_while_a_sprint_is_active_or_a_self_hosting_run_runs(sqlite_session, test_client, enforcement, world):  # noqa: F811
    company = _company(sqlite_session)
    owner, user = _owner(sqlite_session, company.id)
    other = _company(sqlite_session)
    sprint = Sprint(company_id=other.id, sprint_name="busy", status="active")
    sqlite_session.add(sprint)
    sqlite_session.commit()
    shown = _preview(test_client, "land_branch", company.id, owner, {"branch": "feat/x"})
    refused = _start(test_client, "land_branch", company.id, owner, shown)
    assert refused.status_code == 409
    assert refused.json()["detail"]["active_sprint_ids"] == [sprint.id]
    sprint.status = "completed"
    sqlite_session.add(sprint)
    sqlite_session.add(OwnerActionRun(company_id=other.id, action=owner_actions.SELF_HOST,
                                      params={}, started_by_user_id=user.id, observations={}))
    sqlite_session.commit()
    busy = _start(test_client, "land_branch", company.id, owner, shown)
    assert busy.status_code == 409 and "self_host_container_run" in busy.json()["detail"]["message"]
    assert world["launched"] == []


def test_the_digest_covers_what_was_observed(sqlite_session, test_client, enforcement, world):  # noqa: F811
    company = _company(sqlite_session)
    owner, _ = _owner(sqlite_session, company.id)
    shown = _preview(test_client, "land_branch", company.id, owner, {"branch": "feat/x"})
    world["head"] = "e" * 40  # the branch moved after the preview
    moved = _start(test_client, "land_branch", company.id, owner, shown)
    assert moved.status_code == 409
    forged = _start(test_client, "land_branch", company.id, owner, {**shown, "confirmation": "0" * 64})
    assert forged.status_code == 409
    assert world["launched"] == []


def test_a_push_that_is_not_a_fast_forward_is_refused(sqlite_session, test_client, enforcement, world):  # noqa: F811
    company = _company(sqlite_session)
    owner, _ = _owner(sqlite_session, company.id)
    world["push"].update(fast_forward=False, commits=[])
    shown = _preview(test_client, "push_main", company.id, owner, {})
    assert shown["blockers"] == ["not_fast_forward"]
    refused = _start(test_client, "push_main", company.id, owner, shown)
    assert refused.status_code == 409 and refused.json()["detail"]["blockers"] == ["not_fast_forward"]
    assert world["launched"] == []
    assert all(subject != "workspace.self_host.push" for subject, _ in world["requests"])


def test_a_push_shows_the_commits_that_become_public(sqlite_session, test_client, enforcement, world):  # noqa: F811
    company = _company(sqlite_session)
    owner, _ = _owner(sqlite_session, company.id)
    shown = _preview(test_client, "push_main", company.id, owner, {})
    assert shown["effect"][0]["key"] == "public" and shown["facts"]["public"] is True
    assert shown["facts"]["commits"] == world["push"]["commits"] and shown["blockers"] == []
    assert _start(test_client, "push_main", company.id, owner, shown).status_code == 202
    assert world["launched"][0]["facts"]["main"] == MAIN


def test_close_is_refused_when_the_fix_is_not_on_origin_unless_overridden(sqlite_session, test_client, enforcement, world):  # noqa: F811
    company = _company(sqlite_session)
    owner, _ = _owner(sqlite_session, company.id)
    world["fix_on_origin"] = False
    shown = _preview(test_client, "close_issue", company.id, owner, {"issue_number": 9})
    assert shown["blockers"] == ["fix_not_on_origin"]
    assert shown["params"]["fix_commit"] == FIX
    assert "not on origin/main yet" in shown["params"]["comment"]
    assert _start(test_client, "close_issue", company.id, owner, shown).status_code == 409
    assert world["launched"] == []

    overridden = _preview(test_client, "close_issue", company.id, owner,
                          {**shown["params"], "override_not_on_origin": True})
    assert overridden["blockers"] == [] and "override" in [e["key"] for e in overridden["effect"]]
    started = _start(test_client, "close_issue", company.id, owner, overridden)
    assert started.status_code == 202, started.text
    row = sqlite_session.exec(select(AuditLog).where(
        AuditLog.interaction_id == f"owner_action:{started.json()['id']}")).one()
    assert row.details["params"]["override_not_on_origin"] is True


def test_the_comment_confirmed_is_the_comment_the_run_gets(sqlite_session, test_client, enforcement, world):  # noqa: F811
    company = _company(sqlite_session)
    owner, _ = _owner(sqlite_session, company.id)
    suggested = _preview(test_client, "close_issue", company.id, owner, {"issue_number": 9})
    assert suggested["params"]["comment"].startswith(f"Fixed by {FIX} (Fix it (#9)). It is on origin/main.")
    edited = "Fixed on main.\n\nThanks for the report!"
    # Another text with the first preview's digest is not what was confirmed.
    stale = _start(test_client, "close_issue", company.id, owner,
                   {**suggested, "params": {**suggested["params"], "comment": edited}})
    assert stale.status_code == 409
    shown = _preview(test_client, "close_issue", company.id, owner,
                     {**suggested["params"], "comment": edited})
    started = _start(test_client, "close_issue", company.id, owner, shown)
    assert started.status_code == 202, started.text
    assert world["launched"][-1]["params"]["comment"] == edited
