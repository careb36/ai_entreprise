"""Choosing the model by the task's difficulty (issue #319).

- ``services/models/difficulty.py``: the estimate is a pure function of the
  plan, with its inputs and weights recorded, and bands low/medium/high;
- ``services/models/routing_policy.py``: a policy that cannot be read is
  ignored with a warning and the role default kept; a Manager's is checked
  strictly; the company's beats the deployment's;
- ``model_routing.route``: what was requested and why, never the served model;
- ``services/loop/task_routing.py``: the estimate and the routing are stored on
  the task as an ``estimated`` event and reach the run's evidence beside the
  served model;
- ``routing_record``: the stored estimate wins over a recomputation, and the
  numbers are split by band, served model and policy.
"""

from __future__ import annotations

import asyncio
import json
import logging

import pytest
from config import settings
from models import AuditLog, Company, TaskEvent
from models.agent_run import AgentRun, Verification
from models.architecture import Architecture
from models.specification import Specification
from models.sprint import BacklogItem, Sprint
from services.loop import task_routing
from services.loop.worker import deliver as worker_deliver
from services.loop.worker import execute as worker_execute
from services.models import difficulty, model_routing, routing_policy, routing_record
from services.models.model_factory import default_model_for_role
from services.tasks import task_events
from sqlmodel import select
from test_git_workflow import _member, enforcement  # noqa: F401
from test_llm_routing import _board, _Nats, db, llm  # noqa: F401

ARCH = {
    "modules": [
        {"id": "MOD-1", "path": "app/auth.py", "covers": ["REQ-1"]},
        {"id": "MOD-2", "path": "app/session.py", "covers": ["REQ-1"]},
        {"id": "MOD-3", "path": "app/audit.py", "covers": ["REQ-2"]},
        {"id": "MOD-4", "path": "app/other.py", "covers": ["REQ-9"]},
    ]
}


@pytest.fixture(autouse=True)
def _no_policy(monkeypatch):
    monkeypatch.setattr(settings, "AGENT_MODEL_POLICY", "")
    routing_policy._warned.clear()


# ---------------------------------------------------------------------------
# The estimate
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("item", "counts", "score", "band"),
    [
        ({"covers": []}, {}, 1, "low"),  # a chore with no criterion
        ({"covers": ["REQ-1.1"]}, {}, 2, "low"),
        ({"covers": ["REQ-1.1", "REQ-1.2"]}, {}, 3, "medium"),
        ({"covers": ["REQ-1.1", "REQ-1.2", "REQ-2.1"]}, {}, 4, "high"),
        # A second attempt of an easy task is no longer low.
        ({"covers": ["REQ-1.1", "REQ-1.2"]}, {"attempt": 2}, 4, "high"),
        ({"covers": ["REQ-1.1"]}, {"rework_requests": 1}, 3, "medium"),
        # A person's send-back weighs on top of the rework.
        ({"covers": ["REQ-1.1"]}, {"send_backs": 1}, 4, "high"),
        ({"covers": ["REQ-1.1"], "depends_on": [3, 4]}, {}, 3, "medium"),
    ],
)
def test_representative_tasks_fall_in_their_band(item, counts, score, band):
    estimate = difficulty.estimate_item(item, **counts)
    assert (estimate["score"], estimate["band"]) == (score, band)
    assert sum(estimate["weights"].values()) >= estimate["score"]


def test_the_architecture_adds_modules_and_files_and_the_inputs_are_recorded():
    estimate = difficulty.estimate_item(
        {"covers": ["REQ-1.1", "REQ-2.1"], "depends_on": [7]}, ARCH, attempt=1
    )
    assert estimate["inputs"] == {
        "criteria": 2,
        "requirements": 2,
        "modules": ["MOD-1", "MOD-2", "MOD-3"],
        "files": ["app/auth.py", "app/session.py", "app/audit.py"],
        "dependencies": 1,
        "attempt": 1,
        "rework": False,
        "rework_requests": 0,
        "send_backs": 0,
    }
    assert estimate["weights"] == {
        "base": 1,
        "criteria": 2,
        "modules": 2,
        "files": 1,
        "dependencies": 0,
        "rework": 0,
        "send_back": 0,
    }
    # Clamped to 5, and the same plan always gives the same estimate.
    assert estimate["score"] == 5 and estimate["band"] == "high"
    assert estimate == difficulty.estimate_item(
        {"covers": ["REQ-1.1", "REQ-2.1"], "depends_on": [7]}, ARCH, attempt=1
    )


def test_without_architecture_the_score_is_the_one_routing_always_used():
    for covers in ([], ["R1.1"], ["R1.1", "R1.2"], ["R1.1", "R1.2", "R2.1", "R3.1"]):
        for rework in (False, True):
            old = max(1, min(5, 1 + min(len(covers), 3) + int(rework)))
            item = {"covers": covers}
            assert model_routing.estimate_difficulty(item, rework=rework) == old


def test_a_stored_score_is_read_only_when_it_is_one():
    assert difficulty.stored_score({"score": 3}) == 3
    assert difficulty.stored_score({"score": 9}) is None
    assert difficulty.stored_score({"score": True}) is None
    assert difficulty.stored_score(None) is None


# ---------------------------------------------------------------------------
# The policy
# ---------------------------------------------------------------------------


def test_a_policy_that_cannot_be_read_is_ignored_with_a_warning(caplog):
    with caplog.at_level(logging.WARNING, logger="fabrique-task-worker"):
        assert routing_policy.parse("{not json") == {}
        assert routing_policy.parse('["low"]') == {}
        assert routing_policy.parse('{"bands": {"9-12": "x", "": "y"}}') == {}
    assert "not JSON" in caplog.text
    assert 'no "bands" object' in caplog.text
    assert "no readable band" in caplog.text


def test_a_band_that_cannot_be_read_is_dropped_and_the_others_kept(caplog):
    with caplog.at_level(logging.WARNING, logger="fabrique-task-worker"):
        policy = routing_policy.parse(
            {"bands": {"Low": " cheap ", "6-7": "x", "high": "", "3-3": "mid"}}
        )
    assert policy["bands"] == {"low": "cheap", "3-3": "mid"}
    assert policy["label"].startswith("policy-")
    assert "'6-7'" in caplog.text and "'high'" in caplog.text
    assert (
        routing_policy.parse({"bands": {"low": "a"}, "label": "cheap-easy"})["label"]
        == "cheap-easy"
    )


def test_an_unreadable_deployment_policy_keeps_the_role_default(monkeypatch, caplog):
    monkeypatch.setattr(settings, "AGENT_MODEL_POLICY", "{bands: oops")
    agent = {"role": "Developer"}
    with caplog.at_level(logging.WARNING, logger="fabrique-task-worker"):
        decision = model_routing.route(agent, item={"covers": ["R1.1"]})
    assert decision["model"] == default_model_for_role("Developer")
    assert decision["routing"]["reason"] == model_routing.REASON_NO_POLICY
    assert "not JSON" in caplog.text


@pytest.mark.parametrize(
    ("value", "problem"),
    [
        ("low=cheap", "an object"),
        ({"bands": {}}, "at least one band"),
        ({"bands": {"extreme": "x"}}, "not low, medium, high"),
        ({"bands": {"0-3": "x"}}, "not low, medium, high"),
        ({"bands": {"low": ""}}, "a model id or a tier name"),
        ({"bands": {"low": "a\nb"}}, "not a model id"),
        ({"bands": {"low": "a"}, "extra": 1}, "unknown field"),
        ({"bands": {"low": "a"}, "label": 3}, "a short text"),
    ],
)
def test_a_managers_policy_is_checked_strictly(value, problem):
    with pytest.raises(routing_policy.InvalidPolicy, match=problem):
        routing_policy.validate(value)


def test_named_bands_and_tiers_route():
    policy = routing_policy.parse(
        {"bands": {"low": "cheap", "medium": "fast", "high": "strong"}, "label": "p"}
    )
    easy, mid, hard = (
        model_routing.route(
            {"role": "Developer"}, item={"covers": c}, policy=policy, source="company"
        )
        for c in (["R1.1"], ["R1.1", "R1.2"], ["R1.1", "R1.2", "R2.1"])
    )
    assert (easy["model"], easy["routing"]["band"], easy["routing"]["reason"]) == (
        "cheap",
        "low",
        "band",
    )
    # A tier is not a model id the proxy serves: the role default runs it.
    assert mid["model"] == default_model_for_role("Developer")
    assert mid["routing"] == {
        "policy": "p",
        "source": "company",
        "band": "medium",
        "requested": "fast",
        "chosen": default_model_for_role("Developer"),
        "reason": model_routing.REASON_TIER,
    }
    assert hard["model"] == "strong" and hard["difficulty"]["band"] == "high"


def test_a_model_a_person_chose_is_never_re_routed():
    policy = routing_policy.parse({"bands": {"1-5": "cheap"}})
    decision = model_routing.route(
        {"role": "Developer", "model": "chosen"}, item={"covers": []}, policy=policy
    )
    assert decision["model"] == "chosen"
    assert decision["routing"]["reason"] == model_routing.REASON_CONFIGURED
    assert decision["difficulty"]["score"] == 1  # the plan is still observed


def test_without_a_policy_nothing_changes():
    for covers in ([], ["R1.1", "R1.2", "R2.1"]):
        decision = model_routing.route({"role": "QA"}, item={"covers": covers})
        assert decision["model"] == default_model_for_role("QA")
        assert decision["routing"]["policy"] is None
        assert model_routing.resolve_agent_model(
            {"role": "QA"}, item={"covers": covers}
        ) == default_model_for_role("QA")


# ---------------------------------------------------------------------------
# The worker: stored on the task, carried to the run's evidence
# ---------------------------------------------------------------------------


def _with_architecture(session, sprint):
    spec = Specification(company_id=sprint.company_id, title="S", status="approved")
    session.add(spec)
    session.commit()
    session.add(
        Architecture(
            company_id=sprint.company_id,
            specification_id=spec.id,
            title="A",
            status="approved",
            version=1,
            modules=ARCH["modules"],
        )
    )
    sprint.specification_id = spec.id
    session.add(sprint)
    session.commit()


def test_the_estimate_and_routing_are_stored_on_the_task(db, monkeypatch):  # noqa: F811
    monkeypatch.setattr(
        settings, "AGENT_MODEL_POLICY", json.dumps({"bands": {"1-5": "deployed"}})
    )
    company, sprint, item = _board(db)
    item.covers, item.depends_on = ["REQ-1.1", "REQ-2.1"], [1, 2]
    db.add(item)
    company.model_policy = {"bands": {"low": "cheap", "high": "strong"}, "label": "own"}
    db.add(company)
    db.commit()
    _with_architecture(db, sprint)
    task = {"id": item.id, "title": item.title, "covers": item.covers}

    decision = task_routing.route_task(
        task,
        {"role": "Developer"},
        company.id,
        rework=False,
        editor=False,
        engine=db.get_bind(),
    )

    # The company's policy beats the deployment's.
    assert decision["model"] == "strong"
    assert decision["routing"]["source"] == "company"
    assert decision["routing"]["policy"] == "own"
    assert decision["difficulty"]["inputs"]["modules"] == ["MOD-1", "MOD-2", "MOD-3"]
    assert decision["difficulty"]["inputs"]["dependencies"] == 2
    db.expire_all()
    (event,) = db.exec(
        select(TaskEvent).where(
            TaskEvent.task_id == item.id, TaskEvent.event_type == "estimated"
        )
    ).all()
    assert event.actor == "system" and event.task_status is None
    assert event.payload == {
        "difficulty": decision["difficulty"],
        "routing": decision["routing"],
    }
    # An event only: the board's thread is unchanged, and the chain holds.
    assert db.get(BacklogItem, item.id).comments in (None, [])
    assert task_events.verify_chain(db, item.id)["status"] == "intact"


def test_the_attempt_and_rework_history_come_from_the_task(db):  # noqa: F811
    _company, _sprint, item = _board(db)
    item.covers = ["REQ-1.1"]
    db.add(item)
    db.commit()
    task_events.record(
        db, item, "delivered", "Developer", text="first try", author="Developer"
    )
    task_events.record(
        db, item, "reviewed", "QA", text="NEEDS WORK: cover the empty case", author="QA"
    )
    db.commit()
    row = db.get(BacklogItem, item.id)
    estimate = task_routing.estimate_for(db, row, rework=True)
    assert estimate["inputs"]["attempt"] == 2
    assert estimate["inputs"]["rework_requests"] == 1
    assert estimate["band"] == "medium"


def test_a_failed_reading_falls_back_to_the_deployments_routing(monkeypatch, caplog):
    class Broken:
        pass

    with caplog.at_level(logging.WARNING, logger="fabrique-task-worker"):
        decision = task_routing.route_task(
            {"id": 5, "covers": []},
            {"role": "QA"},
            1,
            rework=False,
            editor=False,
            engine=Broken(),
        )
    assert decision["model"] == default_model_for_role("QA")
    assert "Difficulty routing unavailable" in caplog.text


def test_the_run_evidence_carries_difficulty_and_routing_beside_the_served_model(
    llm,  # noqa: F811
    db,  # noqa: F811
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "audit"))
    monkeypatch.setattr(worker_deliver, "_resolve_workspace_root", lambda cid: None)
    monkeypatch.setattr(
        settings,
        "AGENT_MODEL_POLICY",
        json.dumps({"bands": {"low": "cheap-model", "3-5": "strong-model"}}),
    )
    llm.response = {
        "choices": [{"message": {"content": "A written analysis with no files."}}],
        "served_model": "what-the-proxy-served",
        "provider": "qwen",
    }
    company, sprint, item = _board(db, role="Developer", agent_model="")
    agent = {"role": "Developer", "name": "Dev", "model": ""}
    task = {
        "id": item.id,
        "title": item.title,
        "description": item.description,
        "covers": ["REQ-1.1"],
    }
    sprint_dict = {"id": sprint.id, "company_id": company.id, "name": "Routing"}

    deliverable = worker_execute._execute_task(
        task, agent, "Routing", company_id=company.id
    )
    asyncio.run(
        worker_deliver._deliver_artifacts(
            _Nats(), task, agent, sprint_dict, deliverable
        )
    )

    assert llm.calls[0]["model"] == "cheap-model"  # the request
    db.expire_all()
    run = db.exec(
        select(AgentRun).where(AgentRun.external_ref == f"task-{item.id}")
    ).first()
    assert run.model == "what-the-proxy-served"  # the observation, unchanged
    verification = db.exec(
        select(Verification).where(Verification.agent_run_id == run.id)
    ).first()
    evidence = (
        verification.evidence
        if isinstance(verification.evidence, dict)
        else json.loads(verification.evidence)
    )
    assert evidence["difficulty"]["band"] == "low"
    assert evidence["routing"]["chosen"] == "cheap-model"
    assert evidence["routing"]["source"] == "deployment"
    assert "served" not in json.dumps(evidence["routing"])


# ---------------------------------------------------------------------------
# The company's policy through the API
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("enforcement")
def test_a_manager_writes_the_policy_and_a_viewer_reads_it(sqlite_session, test_client):
    company = Company(company_name="Policy Co", autonomy_level=2)
    sqlite_session.add(company)
    sqlite_session.commit()
    manager = _member(sqlite_session, company.id, "Manager")
    viewer = _member(sqlite_session, company.id, "Viewer")
    url = f"/v1/companies/{company.id}/model_settings"
    policy = {"bands": {"low": "fast", "high": "strong"}, "label": "cheap-easy"}

    assert (
        test_client.put(url, json={"model_policy": policy}, headers=viewer).status_code
        == 403
    )
    bad = test_client.put(
        url, json={"model_policy": {"bands": {"x": "y"}}}, headers=manager
    )
    assert bad.status_code == 422 and "not low, medium, high" in bad.text
    conn = test_client.put(
        url, json={"model_policy": {"bands": {"low": "conn:999:m"}}}, headers=manager
    )
    assert conn.status_code == 422
    saved = test_client.put(url, json={"model_policy": policy}, headers=manager)
    assert saved.status_code == 200, saved.text
    body = test_client.get(url, headers=viewer).json()
    assert body["model_policy"] == policy and body["policy_source"] == "company"
    assert body["difficulty_bands"] == ["low", "medium", "high"]
    # A write that does not name the policy keeps it; null clears it.
    test_client.put(url, json={"default_model": None}, headers=manager)
    assert test_client.get(url, headers=viewer).json()["model_policy"] == policy
    test_client.put(url, json={"model_policy": None}, headers=manager)
    assert test_client.get(url, headers=viewer).json()["policy_source"] is None


# ---------------------------------------------------------------------------
# The record: the stored estimate, split by band, model and policy
# ---------------------------------------------------------------------------


def test_the_record_reads_the_stored_estimate_and_splits_by_band_model_and_policy(
    sqlite_session, monkeypatch
):
    session = sqlite_session
    company = Company(company_name="Record Co", autonomy_level=2)
    session.add(company)
    session.commit()
    sprint = Sprint(company_id=company.id, sprint_name="S", status="completed")
    session.add(sprint)
    session.commit()

    def task(covers, status, model, stored=None, policy=None, cost=None):
        item = BacklogItem(sprint_id=sprint.id, title="T", status=status, covers=covers)
        session.add(item)
        session.commit()
        if stored is not None:
            task_events.record(
                session,
                item,
                "estimated",
                "system",
                payload={
                    "difficulty": {"score": stored},
                    "routing": {"policy": policy},
                },
            )
            # A later attempt's estimate does not move the task.
            task_events.record(
                session,
                item,
                "estimated",
                "system",
                payload={"difficulty": {"score": 5}},
            )
        session.add(
            AgentRun(
                company_id=company.id,
                external_ref=f"task-{item.id}",
                version=1,
                producer="le-fabrique-task-worker",
                agent_role="Developer",
                model=model,
                artifact_digest="x",
                artifact_size=1,
            )
        )
        if cost is not None:
            session.add(
                AuditLog(
                    company_id=company.id,
                    interaction_id=f"task-{item.id}",
                    action="execute_task",
                    details={
                        "tokens": "reported",
                        "model": model,
                        "provider": "nowhere",
                    },
                    cost_tokens=cost,
                )
            )
        session.commit()

    # One criterion would be 2 (low); the stored estimate says 4 (high).
    task(["REQ-1.1"], "done", "strong", stored=4, policy="cheap-easy")
    task(["REQ-1.1"], "done", "cheap", stored=1, policy="cheap-easy", cost=50)
    task(["REQ-1.1", "REQ-1.2"], "blocked", "cheap")  # before the event: recomputed, 3

    data = routing_record.record(session, company.id)
    assert [
        (r["difficulty"], r["difficulty_band"], r["model"]) for r in data["rows"]
    ] == [
        (1, "low", "cheap"),
        (3, "medium", "cheap"),
        (4, "high", "strong"),
    ]
    assert [(b["difficulty_band"], b["tasks"]) for b in data["by_difficulty_band"]] == [
        ("low", 1),
        ("medium", 1),
        ("high", 1),
    ]
    by_model = {m["model"]: m for m in data["by_model"]}
    assert by_model["cheap"]["tasks"] == 2 and by_model["cheap"]["blocked"] == 1
    assert by_model["cheap"]["tokens"]["reported"] == 50
    # A turn without a price makes the cost unknown, never 0.
    assert (
        by_model["cheap"]["cost_cents"] is None
        and by_model["cheap"]["cost_unknown"] == 2
    )
    assert [(p["policy"], p["tasks"]) for p in data["by_policy"]] == [
        ("cheap-easy", 2),
        (None, 1),
    ]
    assert data["policy_source"] is None


def test_the_split_sums_known_costs_and_counts_unknown_ones_apart():
    facts = [
        routing_record.task_fact(
            difficulty=1, model="a", status="done", comments=[], runs=1, cents=1.5
        ),
        routing_record.task_fact(
            difficulty=2, model="a", status="done", comments=[], runs=1, cents=2.0
        ),
        routing_record.task_fact(
            difficulty=5, model="b", status="done", comments=[], runs=1
        ),
    ]
    low, high = routing_record.split(facts, "difficulty_band")
    assert (
        low["difficulty_band"],
        low["tasks"],
        low["cost_cents"],
        low["cost_unknown"],
    ) == ("low", 2, 3.5, 0)
    assert (high["difficulty_band"], high["cost_cents"], high["cost_unknown"]) == (
        "high",
        None,
        1,
    )
