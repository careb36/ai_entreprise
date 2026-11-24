"""The job of ``self_host_container_run`` (issue #701, ``docs/design/owner-actions.md``).

Against a faked API and a faked daemon (no model, no harness run, no file of
the user's touched): the run asks the daemon for the checkout by name, drives
the harness's steps with the API it is given, and records as observations the
checkout the daemon reported, the company and sprint ids, the agent run ids
and the sandbox report built from the daemon's replies (asked and observed
apart). The sprint is marked urgent only when asked. A cancel request stops
the run at its next call and stops its sprint; tokens recorded over the
budget do the same and fail the run ``budget_exceeded``. The in-process
principal is honoured only while its run is live and never from a header.
"""

import asyncio
import itertools
import secrets
import time
from pathlib import Path
from types import SimpleNamespace

import audit_signing
import pytest
from models import Company, OwnerActionRun, User
from services.management import owner_action_runner as runner
from services.management import owner_actions, self_host_snapshot
from services.management.in_process_api import asgi_request
from services.platform.agent_service import AgentService
from services.platform.in_process_principal import InProcessPrincipal
from sqlmodel import Session
from test_git_workflow import _member, enforcement  # noqa: F401
from test_self_host_sandbox_mode import CONTAINER_REPLY, HOST_REPLY

REPO = Path(__file__).resolve().parents[2]
HARNESS = self_host_snapshot.load_harness(REPO / "scripts" / "self_host")
SNAPSHOT = "/home/someone/fabrique-self-host/lf-701"


@pytest.fixture(autouse=True)
def _audit(monkeypatch, tmp_path):
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "audit"))
    key = secrets.token_hex(16)
    monkeypatch.setattr(audit_signing, "_configured_key", lambda: key)


class FakeApi:
    """The platform's API as the harness's steps call it, with the job's checks."""

    def __init__(self, before_call, world):
        self.before_call, self.world = before_call, world

    def call(self, method, path, body=None):
        self.before_call()
        self.world["calls"].append((method, path, body))
        hook = self.world.get("on_call")
        if hook:
            hook(method, path)
        if path.startswith("/v1/sprints?company_id="):
            return [
                {
                    "id": 42,
                    "status": self.world["sprint_status"],
                    "integration_state": "none",
                }
            ]
        if path == "/v1/sprints/42/board":
            return {"columns": {"done": [{"title": "T1", "status": "done"}]}}
        if path.startswith("/v1/agent_runs?"):
            return {"agent_runs": [{"id": 12}, {"id": 11}]}
        if path.startswith("/v1/agent_runs/"):
            reply = self.world["sandbox_reply"]
            return {
                "verifications": [
                    {"id": 5, "evidence": {"test_run": {"sandbox": reply}}}
                ]
            }
        return {}

    def get(self, path, raw=False):
        return self.call("GET", path)

    def post(self, path, body=None):
        return self.call("POST", path, body or {})

    def put(self, path, body):
        return self.call("PUT", path, body)


def _harness(session, world):
    """The real harness, its API-heavy steps replaced by fakes that call the API."""

    def step_company(api, args, state, report):
        api.post("/v1/init_company", {"project_path": args.project_path})
        company = Company(company_name="La Fabrique")
        session.add(company)
        session.commit()
        state["company_id"] = company.id
        report["company"] = {"id": company.id, "created": True, "sandbox": args.sandbox}
        world["company_id"] = company.id

    def step_records(api, args, state, report):
        report["records"] = {
            "specification_id": 3,
            "architecture_id": 4,
            "document_matches": args.snapshot.read("docs/ARCHITECTURE.md")
            == "# Arch\n",
            "written_unchanged_in_git": args.snapshot.dirty("docs/ARCHITECTURE.md")
            == "",
        }

    def step_issue(api, args, state, report):
        api.post("/v1/start_sprint", {"scope_size": args.scope})
        state["sprint_id"] = 42
        report["issue"] = {"specification_id": 8, "architecture_id": 9, "sprint_id": 42}
        if world.get("spend"):
            AgentService().audit_interaction(
                session=session,
                company_id=state["company_id"],
                interaction_id="turn",
                agent_id="developer",
                action="llm_turn",
                details={},
                result={},
                decision_signature=None,
                cost_tokens=world["spend"],
            )

    return SimpleNamespace(
        STEPS=HARNESS.STEPS,
        ApiError=HARNESS.ApiError,
        spec_source=HARNESS.spec_source,
        sandbox_mode=HARNESS.sandbox_mode,
        step_company=step_company,
        step_records=step_records,
        step_issue=step_issue,
        step_wait=HARNESS.step_wait,
        step_sandbox=HARNESS.step_sandbox,
    )


async def _daemon(world, subject, payload, timeout):
    world["daemon"].append((subject, payload))
    if subject == self_host_snapshot.CHECKOUT:
        return {
            "ok": True,
            "path": SNAPSHOT,
            "created": True,
            "head": "a" * 40,
            "source_head": "b" * 40,
            "files": 3,
        }
    if subject == self_host_snapshot.READ_AT:
        import base64

        return {
            "ok": True,
            "files": [
                {
                    "path": payload["paths"][0],
                    "status": "present",
                    "content_b64": base64.b64encode(b"# Arch\n").decode(),
                }
            ],
        }
    return {"ok": True, "view": "status", "entries": []}


def _job(session, monkeypatch, **params):
    monkeypatch.setattr(
        HARNESS,
        "time",
        SimpleNamespace(
            sleep=lambda s: None, monotonic=time.monotonic, strftime=time.strftime
        ),
    )
    owner = User(
        email=f"o-{secrets.token_hex(4)}@example.com", full_name="O", password_hash="x"
    )
    company = Company(company_name="Where the Owner acts")
    session.add_all([owner, company])
    session.commit()
    spec = owner_actions.action(owner_actions.SELF_HOST)
    values = owner_actions.validate(
        spec,
        {
            "checkout_name": "lf-701",
            "timeout_seconds": 600,
            "token_budget": 50000,
            **params,
        },
    )
    run = owner_actions.start(
        session,
        company.id,
        owner.id,
        spec,
        values,
        owner_actions.preview(spec, values)["confirmation"],
    )
    world = {
        "calls": [],
        "daemon": [],
        "sprint_status": "completed",
        "sandbox_reply": CONTAINER_REPLY,
    }
    ticks = itertools.count(0, 20)
    job = runner.Job(
        run_id=run.id,
        company_id=company.id,
        user_id=owner.id,
        params=run.params,
        started_at=run.started_at,
        session_factory=lambda: Session(session.get_bind()),
        daemon=lambda s, p, t: _daemon(world, s, p, t),
        make_api=lambda before: FakeApi(before, world),
        harness=_harness(session, world),
        clock=lambda: float(next(ticks)),
    )
    return job, world, run


def test_the_run_records_what_the_platform_observed(sqlite_session, monkeypatch):
    job, world, _run = _job(sqlite_session, monkeypatch)
    finished = asyncio.run(runner.run(job))
    assert finished.status == "succeeded", finished.reason
    assert finished.exit_status == 0
    seen = finished.observations
    # The checkout went to the daemon by name; the backend never named a path.
    assert world["daemon"][0] == (self_host_snapshot.CHECKOUT, {"name": "lf-701"})
    assert seen["checkout"] == {
        "path": SNAPSHOT,
        "created": True,
        "head": "a" * 40,
        "source_head": "b" * 40,
        "files": 3,
    }
    assert ("POST", "/v1/init_company", {"project_path": SNAPSHOT}) in world["calls"]
    assert seen["company_id"] == world["company_id"] and seen["sprint_id"] == 42
    assert seen["agent_run_ids"] == [11, 12]
    assert (
        seen["records_document_matches"] is True
        and seen["records_written_unchanged_in_git"] is True
    )
    sandbox = seen["sandbox"]
    assert (sandbox["asked"], sandbox["observed"], sandbox["observed_by"]) == (
        "container",
        "container",
        "daemon",
    )
    assert sandbox["container_mode"] is True and sandbox["modes"] == {"container": 2}
    assert sandbox["images"] == [CONTAINER_REPLY["image_identity"]["id"]]
    assert seen["steps_done"] == [
        "checkout",
        "company",
        "records",
        "issue",
        "wait",
        "sandbox",
    ]
    assert seen["started_at"] and seen["finished_at"] and seen["exit_status"] == 0
    # Not urgent unless the Owner ticked it.
    assert not any(path.endswith("/urgent") for _, path, _ in world["calls"])
    assert seen["urgent"] is False


def test_a_host_reply_is_not_called_container_mode(sqlite_session, monkeypatch):
    job, world, _ = _job(sqlite_session, monkeypatch)
    world["sandbox_reply"] = HOST_REPLY
    finished = asyncio.run(runner.run(job))
    assert (finished.status, finished.exit_status, finished.reason) == (
        "failed",
        5,
        "not_container_mode",
    )
    assert finished.observations["sandbox"]["asked"] == "container"
    assert finished.observations["sandbox"]["observed"] == "host"


def test_the_sprint_is_urgent_only_when_asked(sqlite_session, monkeypatch):
    job, world, _ = _job(sqlite_session, monkeypatch, urgent=True)
    finished = asyncio.run(runner.run(job))
    assert ("PUT", "/v1/sprints/42/urgent", {"urgent": True}) in world["calls"]
    assert finished.observations["urgent"] is True


def test_cancel_stops_the_run_and_its_sprint(sqlite_session, monkeypatch):
    job, world, run = _job(sqlite_session, monkeypatch)
    world["sprint_status"] = "active"
    polls = itertools.count()

    def cancel_on_second_poll(method, path):
        if path.startswith("/v1/sprints?") and next(polls) == 1:
            with Session(sqlite_session.get_bind()) as s:
                owner_actions.request_cancel(
                    s, s.get(OwnerActionRun, run.id), run.started_by_user_id
                )

    world["on_call"] = cancel_on_second_poll
    finished = asyncio.run(runner.run(job))
    assert (finished.status, finished.reason) == ("cancelled", "cancelled")
    assert ("POST", "/v1/sprints/42/stop", {}) in world["calls"]
    assert finished.observations["sprint_stopped"] is True
    assert "sandbox" not in finished.observations["steps_done"]


def test_tokens_over_the_budget_stop_the_run(sqlite_session, monkeypatch):
    job, world, _ = _job(sqlite_session, monkeypatch)
    world["sprint_status"] = "active"
    world["spend"] = 60000  # recorded by the platform for the company, over 50,000
    finished = asyncio.run(runner.run(job))
    assert (finished.status, finished.reason) == ("failed", "budget_exceeded")
    assert finished.observations["tokens_observed"] == 60000
    assert ("POST", "/v1/sprints/42/stop", {}) in world["calls"]


def test_a_daemon_refusal_fails_the_run_with_its_reason(sqlite_session, monkeypatch):
    from services.workspace.workspace_daemon import DaemonError

    job, world, _ = _job(sqlite_session, monkeypatch)

    async def refuse(subject, payload, timeout):
        raise DaemonError(400, "name resolves outside the root")

    job.daemon = refuse
    finished = asyncio.run(runner.run(job))
    assert finished.status == "failed"
    assert finished.reason == "daemon_refused (400): name resolves outside the root"
    assert world["calls"] == []


def test_the_in_process_principal_is_honoured_only_while_live(
    sqlite_session,
    test_client,
    enforcement,  # noqa: F811
):
    company = Company(company_name="Principal")
    sqlite_session.add(company)
    sqlite_session.commit()
    _member(sqlite_session, company.id, "Owner")
    owner = sqlite_session.exec(
        __import__("sqlmodel").select(User).order_by(User.id.desc())
    ).first()
    path = f"/v1/companies/{company.id}/run_window"
    app = test_client.app
    alive = {"on": True}
    principal = InProcessPrincipal(user_id=owner.id, run_id=1, live=lambda: alive["on"])
    status, _ = asyncio.run(asgi_request(app, principal, "GET", path))
    assert status == 200
    alive["on"] = False
    status, _ = asyncio.run(asgi_request(app, principal, "GET", path))
    assert status == 401
    # A header cannot stand in for the principal.
    forged = test_client.get(
        path, headers={"fabrique.in_process_principal": str(owner.id)}
    )
    assert forged.status_code == 401
