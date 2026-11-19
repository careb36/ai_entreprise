"""
A company's forge policy: git stays local unless an Owner decides otherwise
(issue #727; ``services/contracts/forge_policy.py``,
``services/platform/forge_policy_setting.py``, ``vscode-daemon/forge_gate.py``).

With ``none`` a sprint integrates, tests and merges on a repository that has
an ``origin`` while the daemon runs no ``fetch``, ``push`` or ``ls-remote``
(a recording ``run_git`` and ``subprocess.run`` prove it); with ``none`` or
``fetch`` publishing answers 409 ``forge_disabled`` and the daemon refuses it
even when asked directly. Only an Owner changes the policy, audited on an
intact chain; the migration gives existing companies ``publish`` and a new one
``none``; the deployment's and the daemon's ceilings cap it.
"""

import asyncio
import secrets
import subprocess
import sys
from pathlib import Path

import audit_signing
import pytest
from audit_chain import verify_chain
from config import settings
from models import AuditLog, Company, Sprint
from models.integration import SprintIntegration
from services.contracts import forge_policy
from services.platform import forge_policy_setting
from services.release import git_publish, integration_publish
from sqlmodel import select

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "vscode-daemon"))
import forge_gate
import git_base
import git_diff
import git_workspace
import local_merge
import platform_reviews
import sprint_integration
import test_git_publish
import test_git_workflow
from sprint_integration_helpers import _Daemon, _two_done_tasks, suite  # noqa: F401
from test_git_workflow import _company, _member, daemon, git

enforcement = test_git_workflow.enforcement
published_repo = test_git_publish.published_repo
REMOTE_COMMANDS = {"fetch", "push", "ls-remote", "pull", "clone", "remote-https"}


@pytest.fixture(autouse=True)
def _audit(monkeypatch, tmp_path):
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "audit"))
    key = secrets.token_hex(16)
    monkeypatch.setattr(audit_signing, "_configured_key", lambda: key)


@pytest.fixture
def recorded(monkeypatch):
    """Every git command the daemon runs, and every program it starts."""
    commands: list[list[str]] = []
    real = git_workspace.run_git

    def recording(args, cwd, check=True, timeout=git_workspace.GIT_TIMEOUT_SECONDS):
        commands.append(["git", *args])
        return real(args, cwd, check=check, timeout=timeout)

    for module in (git_workspace, git_base, git_diff, local_merge, sprint_integration):
        monkeypatch.setattr(module, "run_git", recording)
    started = subprocess.run

    def spying(argv, *args, **kwargs):
        if isinstance(argv, list) and argv and argv[0] in ("gh", "glab"):
            commands.append(list(argv))
        return started(argv, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", spying)
    return commands


def _remote_contact(commands):
    """The commands that would reach a remote: git's remote verbs, gh and glab."""
    return [
        c
        for c in commands
        if c[0] in ("gh", "glab") or any(verb in REMOTE_COMMANDS for verb in c[1:])
    ]


class _LocalDaemon(_Daemon):
    """The real daemon's handlers, the local merge and the publication included."""

    async def request(self, subject, payload, timeout=5.0):
        if subject == "workspace.git.merge":
            self.requests.append((subject, payload))
            return local_merge.merge_local(payload["workspace_root"], payload)
        if subject == "workspace.git.reviews":
            self.requests.append((subject, payload))
            return platform_reviews.fetch(payload["workspace_root"], payload)
        return await super().request(subject, payload, timeout)


# --- the values ------------------------------------------------------------------


def test_an_unknown_policy_reads_as_none_and_a_ceiling_caps_it():
    assert forge_policy.parse("publish") == "publish"
    assert [forge_policy.parse(v) for v in (None, "", "PUBLISH", 3)] == ["none"] * 4
    assert forge_policy.cap("publish", "fetch") == "fetch"
    assert forge_policy.cap("fetch", "publish") == "fetch"
    assert forge_policy.cap("publish", "unknown") == "none"
    assert forge_policy.allows_fetch("fetch") and not forge_policy.allows_fetch("none")
    assert forge_policy.allows_publish("publish")
    assert not forge_policy.allows_publish("fetch")
    assert forge_policy.refusal("fetch")["code"] == "forge_disabled"


def test_the_daemon_applies_its_own_ceiling_and_reads_a_missing_policy_as_none(
    monkeypatch,
):
    monkeypatch.delenv("FORGE_POLICY_MAX", raising=False)
    assert forge_gate.of({"forge_policy": "publish"}) == "publish"
    assert forge_gate.of({}) == forge_gate.of(None) == "none"
    assert forge_gate.of({"forge_policy": "anything"}) == "none"
    monkeypatch.setenv("FORGE_POLICY_MAX", "fetch")
    assert forge_gate.of({"forge_policy": "publish"}) == "fetch"
    monkeypatch.setenv("FORGE_POLICY_MAX", "nonsense")
    assert forge_gate.of({"forge_policy": "fetch"}) == "none"


# --- the daemon --------------------------------------------------------------------


def test_with_none_a_sprint_integrates_tests_and_merges_without_touching_the_remote(
    sqlite_session,
    test_client,
    published_repo,
    suite,  # noqa: F811
    recorded,
    monkeypatch,
    enforcement,
):
    root, remote, _ = published_repo
    company, sprint, *_ = _two_done_tasks(sqlite_session, root)
    company.forge_policy = "none"
    sqlite_session.add(company)
    sqlite_session.commit()
    manager = _member(sqlite_session, company.id, "Manager")
    nats = _LocalDaemon()
    monkeypatch.setattr(test_client.app.state, "nats_client", nats)
    remote_main = git(remote, "rev-parse", "main")

    integrated = test_client.post(f"/v1/sprints/{sprint.id}/integrate", headers=manager)
    assert integrated.status_code == 201, integrated.text
    row = integrated.json()
    assert row["status"] == "tests_passed" and row["forge_policy"] == "none"
    assert row["details"]["base_status"]["remote_contacted"] is False
    assert row["details"]["base_status"]["base_remote_not_contacted"] == "origin/main"
    merged = test_client.post(
        f"/v1/sprints/{sprint.id}/integration/merge", headers=manager
    )
    assert merged.status_code == 201, merged.text
    assert git(root, "rev-parse", "main") == row["commit"]

    refused = test_client.post(
        f"/v1/sprints/{sprint.id}/integration/publish", headers=manager
    )
    assert refused.status_code == 409
    assert refused.json()["detail"]["code"] == "forge_disabled"
    # Every git request carried the policy; none reached the remote.
    assert {s for s, _ in nats.requests} >= {"workspace.git.integrate"}
    assert all(
        p.get("forge_policy") == "none"
        for s, p in nats.requests
        if s.startswith("workspace.git.")
    )
    assert "workspace.git.publish" not in {s for s, _ in nats.requests}
    assert recorded and _remote_contact(recorded) == []
    assert git(remote, "rev-parse", "main") == remote_main


@pytest.mark.parametrize("policy", ["none", "fetch", None])
def test_the_daemon_refuses_a_publication_the_policy_does_not_allow(
    published_repo, recorded, policy
):
    root, _, _ = published_repo
    spec = {**test_git_workflow.SPEC, "forge_policy": "fetch"}
    daemon.deliver_files(str(root), [{"path": "a.py", "content": "a = 1\n"}], spec)
    ask = {"branch": spec["branch"], "base": "main"}
    if policy:
        ask["forge_policy"] = policy
    recorded.clear()

    with pytest.raises(git_workspace.ForgeDisabled, match="forge_disabled"):
        git_workspace.publish(str(root), ask)
    with pytest.raises(platform_reviews.ReviewsError) as reviews:
        platform_reviews.fetch(
            str(root),
            {
                "provider": "github",
                "url": "https://github.com/ana/app/pull/1",
                **({"forge_policy": policy} if policy else {}),
            },
        )
    assert (reviews.value.status, reviews.value.code) == (409, "forge_disabled")
    assert recorded == []  # refused before any command ran
    assert git_workspace.error_body(git_workspace.ForgeDisabled("x"))["error"] == {
        "status": 409,
        "message": "x",
        "code": "forge_disabled",
    }


def test_fetch_reads_the_base_and_publish_pushes(published_repo, recorded, monkeypatch):
    root, remote, _ = published_repo
    spec = {**test_git_workflow.SPEC, "forge_policy": "fetch"}
    reply = daemon.deliver_files(
        str(root), [{"path": "a.py", "content": "a = 1\n"}], spec
    )
    assert reply["git"]["remote_contacted"] is True
    assert reply["git"]["forge_policy"] == "fetch"
    assert [c for c in _remote_contact(recorded) if "fetch" in c]
    recorded.clear()

    published = git_workspace.publish(
        str(root), {"branch": spec["branch"], "base": "main", "forge_policy": "publish"}
    )
    assert published["remote_contacted"] is True
    assert (
        git(remote, "rev-parse", f"refs/heads/{spec['branch']}") == published["commit"]
    )
    # The daemon's own ceiling holds even when the backend asks for publish.
    monkeypatch.setenv("FORGE_POLICY_MAX", "fetch")
    with pytest.raises(git_workspace.ForgeDisabled):
        git_workspace.publish(
            str(root),
            {"branch": spec["branch"], "base": "main", "forge_policy": "publish"},
        )


def test_the_base_status_contacts_no_remote_without_fetch(published_repo, recorded):
    root, _, _ = published_repo
    status = git_base.status(str(root), "", "none")
    assert (status["remote_contacted"], status["forge_policy"]) == (False, "none")
    assert status["base_remote"] is None
    assert asyncio.run(_ask_status(root, None))["remote_contacted"] is False
    assert _remote_contact(recorded) == []


async def _ask_status(root, policy):
    answers = []

    async def reply(_nc, _msg, body):
        answers.append(body)

    data = {"workspace_root": str(root), "base": ""}
    if policy:
        data["forge_policy"] = policy
    await git_base.handle_status(None, None, reply, lambda _msg: data)
    return answers[0]


# --- the backend -------------------------------------------------------------------


def test_only_an_owner_changes_the_policy_audited_on_an_intact_chain(
    sqlite_session, test_client, enforcement
):
    company = _company(sqlite_session)
    viewer = _member(sqlite_session, company.id, "Viewer")
    manager = _member(sqlite_session, company.id, "Manager")
    owner = _member(sqlite_session, company.id, "Owner")
    url = f"/v1/companies/{company.id}/forge_policy"

    read = test_client.get(url, headers=viewer)
    assert read.status_code == 200
    assert read.json() == {
        "company_id": company.id,
        "forge_policy": "none",
        "effective": "none",
        "ceiling": "publish",
        "policies": ["none", "fetch", "publish"],
    }
    for who in (viewer, manager):
        denied = test_client.put(url, json={"forge_policy": "publish"}, headers=who)
        assert denied.status_code == 403
    bad = test_client.put(url, json={"forge_policy": "push"}, headers=owner)
    assert bad.status_code == 422

    changed = test_client.put(url, json={"forge_policy": "fetch"}, headers=owner)
    assert changed.status_code == 200, changed.text
    assert changed.json()["forge_policy"] == changed.json()["effective"] == "fetch"
    again = test_client.put(url, json={"forge_policy": "fetch"}, headers=owner)
    assert again.status_code == 200  # no change: no second row

    rows = sqlite_session.exec(
        select(AuditLog).where(
            AuditLog.company_id == company.id,
            AuditLog.action == "forge_policy_changed",
        )
    ).all()
    assert len(rows) == 1
    assert rows[0].result == {"from": "none", "to": "fetch"}
    assert rows[0].details["user_id"] is not None
    assert rows[0].details["token_id"] is None
    assert verify_chain(sqlite_session, company.id)["status"] == "intact"


def test_the_deployment_ceiling_caps_the_policy_and_refuses_a_wider_one(
    sqlite_session, test_client, enforcement, monkeypatch
):
    company = _company(sqlite_session, forge="publish")
    owner = _member(sqlite_session, company.id, "Owner")
    url = f"/v1/companies/{company.id}/forge_policy"
    monkeypatch.setattr(settings, "FORGE_POLICY_MAX", "fetch")

    state = test_client.get(url, headers=owner).json()
    assert (state["forge_policy"], state["effective"]) == ("publish", "fetch")
    refused = test_client.put(url, json={"forge_policy": "publish"}, headers=owner)
    assert refused.status_code == 409 and "FORGE_POLICY_MAX" in refused.json()["detail"]
    assert forge_policy_setting.stamp(sqlite_session, company.id, {"a": 1}) == {
        "a": 1,
        "forge_policy": "fetch",
    }


@pytest.mark.parametrize("policy", ["none", "fetch"])
def test_publishing_a_run_or_an_integration_is_refused_below_publish(
    sqlite_session, test_client, published_repo, enforcement, monkeypatch, policy
):
    root, _, _ = published_repo
    company = _company(sqlite_session, forge=policy)
    manager = _member(sqlite_session, company.id, "Manager")
    run, _ = test_git_publish._delivered_run(sqlite_session, company, root)
    sprint = Sprint(company_id=company.id, sprint_name="S", status="completed")
    sqlite_session.add(sprint)
    sqlite_session.commit()
    sqlite_session.add(_integration_row(company, sprint))
    sqlite_session.commit()
    nats = _LocalDaemon()
    monkeypatch.setattr(test_client.app.state, "nats_client", nats)

    for url in (
        f"/v1/agent_runs/{run.id}/publish?company_id={company.id}",
        f"/v1/sprints/{sprint.id}/integration/publish",
    ):
        refused = test_client.post(url, headers=manager)
        assert refused.status_code == 409, refused.text
        detail = refused.json()["detail"]
        assert (detail["code"], detail["forge_policy"]) == ("forge_disabled", policy)
    assert nats.requests == []  # the daemon was not even asked


def _integration_row(company, sprint, policy="publish"):
    return SprintIntegration(
        company_id=company.id,
        sprint_id=sprint.id,
        trigger="manual",
        requested_by="test",
        status="tests_passed",
        branch=f"fabrique/sprint-{sprint.id}",
        base="main",
        repository="/srv/app",
        commit="a" * 40,
        tasks=[],
        forge_policy=policy,
    )


def test_a_published_integration_names_its_policy_and_a_run_spec_carries_it(
    sqlite_session,
):
    company = _company(sqlite_session, forge="publish")
    sprint = Sprint(company_id=company.id, sprint_name="S", status="completed")
    sqlite_session.add(sprint)
    sqlite_session.commit()
    row = _integration_row(company, sprint)
    sqlite_session.add(row)
    sqlite_session.commit()

    spec = integration_publish.publish_spec(sqlite_session, row)
    assert spec["forge_policy"] == "publish"
    assert "Forge policy at integration: `publish`." in spec["body"]
    company.forge_policy = "fetch"
    sqlite_session.add(company)
    sqlite_session.commit()
    with pytest.raises(git_publish.PublishRefused) as refused:
        integration_publish.publish_spec(sqlite_session, row)
    assert refused.value.body["code"] == "forge_disabled"
    assert "fetch" in refused.value.detail


def test_existing_companies_read_publish_after_the_migration_and_a_new_one_none(
    tmp_path, monkeypatch
):
    import migrations
    from sqlalchemy import text
    from sqlmodel import Session, create_engine

    from alembic import command

    url = f"sqlite:///{tmp_path / 'forge.sqlite'}"
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", url)
    engine = create_engine(url)
    config = migrations.alembic_config()
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0052")
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO companies (company_name, default_scope_size, "
                "autonomy_level, git_workflow) VALUES ('Before', 'M', 2, 1)"
            )
        )
    migrations.upgrade_to_head(engine)
    assert migrations.schema_differences(engine) == []
    with Session(engine) as session:
        session.add(Company(company_name="After"))
        session.commit()
        policies = {
            c.company_name: c.forge_policy for c in session.exec(select(Company)).all()
        }
    assert policies == {"Before": "publish", "After": "none"}
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO companies (company_name, "
                "default_scope_size, autonomy_level, git_workflow) "
                "VALUES ('Raw', 'M', 2, 1)"
            )
        )
        raw = connection.execute(
            text("SELECT forge_policy FROM companies WHERE company_name = 'Raw'")
        ).scalar_one()
    assert raw == "none"  # the column's own default, for a row written in SQL
