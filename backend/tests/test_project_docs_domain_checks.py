"""More computed checks per PMBOK domain (issue #692, ``project_docs/domain_checks.py``
over ``project_docs/collect_checks.py``).

EARS criteria, each a test below:

- When every sprint planned from a specification started after that
  specification version was approved, the Governance check shall be met; when
  one started before (or on a version never approved) it shall be not met;
  when no such sprint has started, or the version is not in the records, it
  shall be not computable and say why.
- The Scope check shall measure the tasks that joined a sprint after it was
  planned (accepted from an intake request, read from the platform's own task
  event), over the sprints' tasks; without tasks or without the record it
  shall be not computable.
- The Schedule check shall measure the completed sprints that closed on or
  before the end their recorded start date and length give; when no completed
  sprint has a planned length it shall be not computable, never a guess.
- The Finance check shall measure the tokens used against the business units'
  configured token quota; when no unit has a quota it shall be not computable.
- The Resources check shall be met when every task of a sprint with a
  recorded team goes to a role on that team, not met otherwise, and not
  computable when no sprint records a team.
- Each check shall cite the records it read, read no claim, and be written in
  the pack's language.
"""

import copy
import secrets

from project_docs_helpers import _empty, _snapshot
from services.planning.project_docs import checks, collect_checks, domain_checks

NEW = ("approved_first", "churn", "on_time", "quota", "sprint_team")


def _basis(**over):
    out = {
        "sprint_lengths": [{"sprint_id": 7, "duration_days": 2}],
        "intake_tasks": [
            {
                "task_id": 42,
                "sprint_id": 7,
                "event_id": 120,
                "chain_hash": "unsigned:" + "6" * 64,
                "created_at": "2026-11-03T15:00:00",
            }
        ],
        "quotas": [
            {"unit_id": 1, "limit": 100000, "used": 25000, "sha256": "aa" * 32},
            {"unit_id": 2, "limit": 0, "used": 9000, "sha256": "bb" * 32},
        ],
    }
    out.update(over)
    return out


def _snap(**over):
    snap = copy.deepcopy(_snapshot())
    snap[collect_checks.KEY] = _basis()
    snap.update(over)
    return snap


def _run(snap):
    return {r["id"]: r for r in checks.run(snap)}


def test_the_new_checks_sit_in_their_domains():
    results = _run(_snap())
    domains = {k: results[k]["domain"] for k in NEW}
    assert domains == {
        "approved_first": "Governance",
        "churn": "Scope",
        "on_time": "Schedule",
        "quota": "Finance",
        "sprint_team": "Resources",
    }
    assert collect_checks.KEY in checks.CHECK_INPUTS


# --- Governance ----------------------------------------------------------------------


def test_work_starts_after_the_specification_is_approved():
    result = _run(_snap())["approved_first"]
    assert result["result"] == "met"
    assert result["observed"] == (
        "1 of 1 sprints planned from a specification started after its approval; "
        "0 were not planned from one"
    )
    assert "\\[S" in result["marks"]


def test_a_sprint_started_before_its_approval_is_not_met():
    snap = _snap()
    snap["history"][1]["approved_at"] = "2026-11-03T10:00:00"
    assert _run(snap)["approved_first"]["result"] == "not met"
    never = _snap()
    never["history"][1]["approved_at"] = None
    assert _run(never)["approved_first"]["result"] == "not met"


def test_without_a_started_planned_sprint_approval_order_is_not_computable():
    snap = _snap()
    snap["sprints"][0]["specification_id"] = None
    result = _run(snap)["approved_first"]
    assert result["result"] == "not computable"
    assert result["observed"] == "no sprint planned from a specification has started"
    lost = _snap()
    lost["history"] = []
    result = _run(lost)["approved_first"]
    assert result["result"] == "not computable"
    assert "not in the records" in result["observed"]
    assert _run(_empty())["approved_first"]["result"] == "not computable"


# --- Scope ---------------------------------------------------------------------------


def test_tasks_added_after_planning_are_measured():
    result = _run(_snap())["churn"]
    assert result["result"] == "measured"
    assert result["observed"] == (
        "1 of 2 tasks joined their sprint after it was planned, from an intake request"
    )
    none = _run(_snap(**{collect_checks.KEY: _basis(intake_tasks=[])}))["churn"]
    assert none["observed"].startswith("0 of 2")


def test_churn_without_tasks_or_the_record_is_not_computable():
    assert _run(_snapshot())["churn"]["result"] == "not computable"
    assert (
        _run(_snapshot())["churn"]["observed"] == "the records it needs were not read"
    )
    empty = {**_empty(), collect_checks.KEY: _basis(intake_tasks=[])}
    assert _run(empty)["churn"]["result"] == "not computable"


# --- Schedule ------------------------------------------------------------------------


def test_sprints_closed_by_their_planned_end_are_measured():
    # Started 2026-11-03, two days: the last planned day is 2026-11-04.
    result = _run(_snap())["on_time"]
    assert result["result"] == "measured"
    assert result["observed"] == (
        "1 of 1 completed sprints with a planned length closed on or before its "
        "planned end; 0 have no planned length"
    )
    late = _snap(
        **{
            collect_checks.KEY: _basis(
                sprint_lengths=[{"sprint_id": 7, "duration_days": 1}]
            )
        }
    )
    assert _run(late)["on_time"]["observed"].startswith("0 of 1")


def test_without_a_planned_length_on_time_is_not_computable():
    snap = _snap(
        **{
            collect_checks.KEY: _basis(
                sprint_lengths=[{"sprint_id": 7, "duration_days": None}]
            )
        }
    )
    result = _run(snap)["on_time"]
    assert result["result"] == "not computable"
    assert result["observed"] == (
        "no completed sprint has a planned length recorded (start date and days)"
    )
    assert _run(_snapshot())["on_time"]["result"] == "not computable"
    assert _run(_empty())["on_time"]["result"] == "not computable"


# --- Finance -------------------------------------------------------------------------


def test_usage_against_the_configured_quota_is_measured():
    result = _run(_snap())["quota"]
    assert result["result"] == "measured"
    assert result["observed"] == (
        "25000 of 100000 tokens of the configured quota used this period "
        "(1 business units with a quota; 0 reached it)"
    )
    assert "\\[S" in result["marks"]


def test_without_a_quota_spend_against_budget_is_not_computable():
    snap = _snap(
        **{
            collect_checks.KEY: _basis(
                quotas=[{"unit_id": 2, "limit": 0, "used": 9000, "sha256": "bb" * 32}]
            )
        }
    )
    result = _run(snap)["quota"]
    assert result["result"] == "not computable"
    assert result["observed"] == (
        "no business unit has a token quota configured (0 means unlimited)"
    )
    assert _run(_snapshot())["quota"]["result"] == "not computable"


# --- Resources -----------------------------------------------------------------------


def test_tasks_go_to_their_sprints_team():
    snap = _snap()
    snap["sprints"][0]["team"] = ["Developer", "DevOps"]
    result = _run(snap)["sprint_team"]
    assert result["result"] == "met"
    assert (
        result["observed"] == "2 of 2 tasks of sprints with a team go to a role on it"
    )
    snap["sprints"][0]["team"] = ["Developer"]
    assert _run(snap)["sprint_team"]["result"] == "not met"


def test_without_a_sprint_team_the_check_is_not_computable():
    result = _run(_snap())["sprint_team"]
    assert result["result"] == "not computable"
    assert result["observed"] == "no sprint records a team"


# --- the document --------------------------------------------------------------------


def test_the_checks_read_no_claim_and_follow_the_language():
    snap = _snap()
    snap["retrospectives"][0]["body"] = "Every sprint was on time and within budget."
    assert [(_run(snap)[k]["result"]) for k in NEW] == [
        _run(_snap())[k]["result"] for k in NEW
    ]
    es = _snap(language="es")
    results = _run(es)
    assert results["approved_first"]["outcome"] == (
        "El trabajo empieza sobre un plan aprobado"
    )
    assert results["quota"]["observed"].startswith("25000 de 100000")
    doc = checks.document(es)
    assert "Cuota de la unidad de negocio" in doc["markdown"]
    assert results["sprint_team"]["outcome"] in doc["missing"]
    assert domain_checks.MET == checks.MET
    assert domain_checks.NOT_COMPUTABLE == checks.NOT_COMPUTABLE


# --- the snapshot --------------------------------------------------------------------


def test_the_snapshot_reads_the_platforms_records_only(sqlite_session):
    from models import BacklogItem, BusinessUnit, Company, Sprint
    from services.planning.project_docs import collect
    from services.tasks import task_events

    company = Company(company_name=f"Checks {secrets.token_hex(3)}", autonomy_level=2)
    sqlite_session.add(company)
    sqlite_session.commit()
    unit_name = f"Cost centre {secrets.token_hex(3)}"
    sqlite_session.add(
        BusinessUnit(
            company_id=company.id,
            unit_name=unit_name,
            token_quota_limit=5000,
            token_quota_used=1200,
        )
    )
    sprint = Sprint(
        company_id=company.id,
        sprint_name="One",
        start_date="2026-11-03",
        duration_days=5,
    )
    sqlite_session.add(sprint)
    sqlite_session.commit()
    planned = BacklogItem(sprint_id=sprint.id, title="Planned")
    joined = BacklogItem(sprint_id=sprint.id, title="Joined")
    sqlite_session.add_all([planned, joined])
    sqlite_session.commit()
    task_events.record(
        sqlite_session,
        joined,
        "note",
        task_events.SYSTEM,
        text="Created from intake request #9.",
        payload={"intake_item_id": 9, "source": "api"},
        company_id=company.id,
    )
    # The same payload under an agent's name is its account, not the record.
    task_events.record(
        sqlite_session,
        planned,
        "note",
        "Developer",
        text="I came from intake.",
        payload={"intake_item_id": 10},
        company_id=company.id,
    )
    sqlite_session.commit()

    basis = collect.snapshot(sqlite_session, company)[collect_checks.KEY]
    assert basis["sprint_lengths"] == [{"sprint_id": sprint.id, "duration_days": 5}]
    [row] = basis["intake_tasks"]
    assert row["task_id"] == joined.id and row["sprint_id"] == sprint.id
    assert row["chain_hash"]
    [quota] = basis["quotas"]
    assert (quota["limit"], quota["used"]) == (5000, 1200)
    assert len(quota["sha256"]) == 64
    assert unit_name not in repr(basis)
