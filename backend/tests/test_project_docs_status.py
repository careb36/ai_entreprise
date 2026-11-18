"""The documentation pack's status and work performance report (issue #530,
phase 2, ``project_docs/status_docs.py`` over ``project_docs/performance.py``).

EARS criteria, each a test below:

- When a pack is compiled, the report shall give, per sprint, the tasks done
  against those planned, the tasks carried over, the Verifier's readings by
  verdict, the rework the backend observed and the model spend the LLM proxy
  reported, each figure with the record it came from.
- The report shall not read a retrospective, an agent message or any other
  claim: changing one leaves its bytes and its input digest unchanged.
- Where no effort observation or no reported usage exists, the report shall
  say so (``missing``) instead of a figure, and never show a zero for an
  unknown spend.
- When a record it read changes (an effort observation, a sprint's spend, a
  verification, a carry), the lint shall flag the report as stale.
- The forecast and the decisions asked of the sponsor shall stay a person's
  section (a template, or a person's statement quoted as a claim).
- The check results shall measure carry-over and first-pass deliveries and
  test that every completed sprint has its spend known and its tasks'
  effort observed.
- The snapshot shall keep only effort observations the backend wrote (actor
  ``system``, ``observed_by`` ``backend``), the latest per task.
"""

import copy
import re

from project_docs_helpers import _docs, _empty, _Pack, _snapshot
from services.planning.project_docs import (
    checks,
    pack,
    performance,
    statements,
    status_docs,
)


def _in(lang, snapshot=None):
    out = copy.deepcopy(snapshot or _snapshot())
    out["language"] = lang
    return out


# --- the figures ------------------------------------------------------------------


def test_the_figures_per_sprint_come_from_the_records():
    [row] = performance.sprint_rows(_snapshot())
    assert row["sprint"]["id"] == 7
    assert (row["planned"], row["done"], row["blocked"]) == (2, 1, 1)
    assert (row["carried_out"], row["carried_in"]) == (1, 0)
    assert row["readings"] == {"verified": 1, "refuted": 1, "unverifiable": 0}
    assert row["high_risk"] == 1
    assert (row["observed"], row["done_observed"], row["first_pass"]) == (2, 1, 0)
    assert (row["rework_cycles"], row["rework_requests"]) == (1, 1)
    assert (row["send_backs"], row["review_failures"]) == (0, 1)
    assert (row["blocks"], row["escalations"], row["risk_holds"]) == (1, 1, 0)
    assert row["cost"]["cost_cents"] == 34.5
    totals = performance.totals(_snapshot())
    assert totals["planned"] == 2 and totals["done"] == 1
    assert totals["cost_cents"] == 34.5 and totals["cost_known"] is True
    assert performance.usd(34.5) == "0.35" and performance.usd(0) == "0.00"


def test_an_unknown_spend_is_never_a_zero():
    snapshot = _snapshot()
    snapshot["costs"] = [
        {
            "sprint_id": 7,
            "turns": 3,
            "reported_turns": 0,
            "estimated_turns": 3,
            "unavailable_turns": 0,
            "reported_tokens": 0,
            "cost_cents": None,
            "reason": "no_reported_usage",
        }
    ]
    totals = performance.totals(snapshot)
    assert totals["cost_known"] is False and totals["cost_cents"] is None
    report = status_docs.report(snapshot)
    row = next(
        line
        for line in report["markdown"].split("## Model spend by sprint")[1].splitlines()
        if line.startswith("| 7 ")
    )
    assert "USD" not in row and "0.00" not in row
    assert "the provider reported no usage" in row


# --- the document -----------------------------------------------------------------


def test_the_report_is_placed_cited_and_compiled_in_the_pack():
    docs, _ = _docs(_snapshot())
    report = docs["status_report"]
    assert report["title"] == "Status and work performance report"
    assert (report["domain"], report["focus_area"]) == (
        "Governance",
        "Monitoring and Controlling",
    )
    md = report["markdown"]
    assert "PMBOK Guide, 8th edition (PMI, 2025)" in md
    assert "| Tasks done against planned | 1 of 2 |" in md
    assert "| Tasks carried to a later sprint | 1 of 2 |" in md
    assert "| Model spend | USD 0.35 (12000 reported tokens) |" in md
    assert "0 of 1 done tasks were accepted on their first delivery" in md
    kinds = {s["kind"] for s in report["sources"]}
    assert {"sprint", "event", "verification", "cost"} <= kinds
    # The effort observation is cited with the task event's chain hash.
    assert "unsigned:" + "4" * 64 in md
    cost = next(s for s in report["sources"] if s["kind"] == "cost")
    assert cost["id"] == "7" and len(cost["sha256"]) == 64
    assert report["missing"] == []
    assert report["persons"] == ["Forecast and decisions requested"]
    assert "status_report.md" in docs["index"]["markdown"]
    marks = set(re.findall(r"S(\d+)", " ".join(re.findall(r"\\\[(S[\d, S]+)\\\]", md))))
    assert marks == {s["mark"][1:] for s in report["sources"]}


def test_the_report_reads_no_claim():
    snapshot = _snapshot()
    report = status_docs.report(snapshot)
    assert "retrospectives" not in report["inputs"]
    changed = copy.deepcopy(snapshot)
    changed["retrospectives"][0]["body"] = "All ten tasks passed every test."
    changed["procedures"] = []
    again = status_docs.report(changed)
    assert again["markdown"] == report["markdown"]
    assert again["input_sha256"] == report["input_sha256"]
    assert "ten tasks" not in again["markdown"]
    assert all(not s.get("claim") for s in report["sources"])


def test_without_observations_the_report_says_what_is_missing():
    report = status_docs.report(_empty())
    assert report["missing"] == ["sprints", "effort observations", "model spend"]
    assert "no sprint is recorded" in report["markdown"]
    snapshot = _snapshot()
    snapshot["effort"], snapshot["costs"] = [], []
    partial = status_docs.report(snapshot)
    assert partial["missing"] == ["effort observations", "model spend"]
    assert "written by the backend when a sprint closes" in partial["markdown"]


def test_the_lint_flags_the_report_when_a_record_it_read_changes():
    base = _snapshot()
    stored = _Pack(*pack.build_documents(base))
    assert pack.lint(stored, base)["ok"]

    def stale(change):
        current = copy.deepcopy(base)
        change(current)
        return {d["key"] for d in pack.lint(stored, current)["documents"] if d["stale"]}

    def more_rework(s):
        s["effort"][0]["observed"]["rework_cycles"] = 3

    def spend(s):
        s["costs"][0]["cost_cents"] = 99.0

    def retro(s):
        s["retrospectives"][0]["body"] = "Another claim."

    assert "status_report" in stale(more_rework)
    assert "status_report" in stale(spend)
    assert "status_report" not in stale(retro)


def test_the_forecast_is_a_persons_section():
    plain = status_docs.report(_snapshot())
    assert "**For a person to complete.**" in plain["markdown"]
    assert "status_report.forecast" in statements.SECTIONS
    filled = copy.deepcopy(_snapshot())
    filled[statements.key_of("status_report")] = {
        "status_report.forecast": {
            "id": 9,
            "section": "status_report.forecast",
            "text": "We expect to finish next sprint.",
            "author_role": "Owner",
            "created_at": "2026-11-17T14:03:59",
            "statement_sha256": "d" * 64,
        }
    }
    report = status_docs.report(filled)
    assert "We expect to finish next sprint." in report["markdown"]
    assert report["statements"][0]["section"] == "status_report.forecast"
    assert report["input_sha256"] != plain["input_sha256"]
    # A statement is quoted as a claim and never moves a figure.
    assert any(s.get("claim") for s in report["sources"])
    assert (
        report["markdown"].split("## Summary")[1].split("##")[0]
        == plain["markdown"].split("## Summary")[1].split("##")[0]
    )


def test_the_report_in_spanish():
    report = status_docs.report(_in("es"))
    assert report["title"] == "Informe de estado y desempeño del trabajo"
    md = report["markdown"]
    assert "| Tareas terminadas sobre las planificadas | 1 de 2 |" in md
    assert "USD 0.35" in md
    assert report["persons"] == ["Pronóstico y decisiones solicitadas"]
    assert status_docs.report(_in("es", _empty()))["missing"] == [
        "sprints",
        "observaciones de esfuerzo",
        "gasto en modelos",
    ]


# --- the computed checks ----------------------------------------------------------


def test_the_checks_measure_carry_over_rework_spend_and_effort():
    results = {r["id"]: r for r in checks.run(_snapshot())}
    assert results["carry_over"]["result"] == "measured"
    assert results["carry_over"]["observed"] == (
        "1 of 2 tasks were carried to a later sprint"
    )
    assert results["first_pass"]["result"] == "measured"
    assert results["first_pass"]["observed"] == (
        "0 of 1 done tasks were accepted on their first delivery"
    )
    assert results["spend"]["result"] == "met"
    assert results["effort"]["result"] == "met"
    assert results["spend"]["domain"] == "Finance"
    unknown = _snapshot()
    unknown["costs"][0]["cost_cents"] = None
    unknown["costs"][0]["reason"] = "no_turns"
    unknown["effort"] = unknown["effort"][:1]
    after = {r["id"]: r["result"] for r in checks.run(unknown)}
    assert after["spend"] == "not met" and after["effort"] == "not met"
    empty = {r["id"]: r["result"] for r in checks.run(_empty())}
    assert {empty[k] for k in ("carry_over", "first_pass", "spend", "effort")} == {
        "not computable"
    }


# --- the snapshot -----------------------------------------------------------------


def test_the_snapshot_keeps_only_the_backends_latest_observation(sqlite_session):
    import secrets

    from models import BacklogItem, Company, Sprint
    from services.planning.project_docs import collect
    from services.tasks import task_events

    company = Company(company_name=f"Status {secrets.token_hex(3)}", autonomy_level=2)
    sqlite_session.add(company)
    sqlite_session.commit()
    sprint = Sprint(company_id=company.id, sprint_name="One", status="completed")
    sqlite_session.add(sprint)
    sqlite_session.commit()
    item = BacklogItem(sprint_id=sprint.id, title="Build", status="done")
    sqlite_session.add(item)
    sqlite_session.commit()

    def observe(actor, rework, by="backend"):
        task_events.record(
            sqlite_session,
            item,
            "effort_observed",
            actor,
            payload={
                "version": 1,
                "sprint_id": sprint.id,
                "observed_by": by,
                "observed": {
                    "done": True,
                    "deliveries": 1 + rework,
                    "rework_cycles": rework,
                },
            },
            company_id=company.id,
        )
        sqlite_session.commit()

    observe(task_events.SYSTEM, 2)
    observe(task_events.SYSTEM, 1)
    observe("Developer", 0)  # an agent's account: a claim, not read
    observe(task_events.SYSTEM, 0, by="Developer")
    snap = collect.snapshot(sqlite_session, company)
    [entry] = snap["effort"]
    assert entry["task_id"] == item.id and entry["sprint_id"] == sprint.id
    assert entry["observed"]["rework_cycles"] == 1 and entry["chain_hash"]
    [cost] = snap["costs"]
    assert cost["sprint_id"] == sprint.id
    assert cost["cost_cents"] is None and cost["reason"] == "no_turns"
    assert snap[statements.key_of("status_report")] == {}
