"""The documentation pack's final report (issue #530, phase 2,
``project_docs/final_docs.py`` over ``project_docs/closure.py``).

EARS criteria, each a test below:

- When a pack is compiled, the final report shall list every requirement of
  the documented specifications as delivered (a covering task is done) or not
  delivered (planned and not done, or never planned), citing the done tasks
  and the Verifier's latest reading of each.
- The report shall give the measured outcomes from the records the status
  report reads (tasks done against planned, carry-over, the Verifier's
  readings, first-pass deliveries, rework, model spend), each cited.
- The report shall list the releases cut, cited with the audit row's chain
  hash; where none was cut, it shall say so.
- The report shall list the holds and risks left open at closure (a blocked
  task still blocked, an escalation with no decision, a risk hold, a
  high-risk reading of a task not done), each cited, and leave out what was
  done or carried to a later iteration by the platform.
- While an iteration is still open, the report shall say it is interim.
- Lessons shall appear only as a pointer to the lessons learned register:
  the report shall not read a retrospective or a procedure, so changing one
  leaves its bytes and its input digest unchanged.
- The sponsor's acceptance, the benefits outlook and the handover notes
  shall stay a person's sections (a template, or a statement quoted as a
  claim), and a statement shall never move a figure.
- When a record it read changes (a verification, an effort observation, a
  release, a carry), the lint shall flag the report as stale.
- The check results shall say whether anything is left on hold at closure:
  met or not met once every iteration is closed, measured while one is open.
"""

import copy
import re

from project_docs_helpers import _docs, _empty, _Pack, _snapshot
from services.planning.project_docs import (
    checks,
    closure,
    final_docs,
    pack,
    statements,
)


def _in(lang, snapshot=None):
    out = copy.deepcopy(snapshot or _snapshot())
    out["language"] = lang
    return out


def _section(md, heading):
    return md.split(heading, 1)[1].split("\n## ", 1)[0]


def _held():
    """The fixture with task 42 still blocked and not carried."""
    snapshot = _snapshot()
    snapshot["carries"] = []
    return snapshot


# --- the figures ------------------------------------------------------------------


def test_the_closure_figures_come_from_the_records():
    snapshot = _snapshot()
    [req1, req2] = closure.requirement_outcomes(snapshot, snapshot["specification"])
    assert req1["id"] == "REQ-1" and req1["outcome"] == closure.DELIVERED
    assert [t["id"] for _, t in req1["done"]] == [41]
    assert [v["verification_id"] for v in req1["readings"]] == [12]
    assert req2["outcome"] == closure.NOT_DELIVERED
    assert req2["why"] == closure.NOT_PLANNED and req2["done"] == []
    # Task 42 was blocked and escalated, then carried by the platform: followed there.
    assert closure.open_holds(snapshot) == []
    # The high-risk reading is of task 41, which is done.
    assert closure.open_risks(snapshot) == []
    assert closure.open_iterations(snapshot) == []
    totals = closure.totals(snapshot)
    assert (totals["requirements"], totals["delivered"]) == (2, 1)
    assert (totals["open_holds"], totals["open_risks"], totals["releases"]) == (0, 0, 1)


def test_holds_and_risks_left_open_are_those_neither_done_nor_carried():
    snapshot = _held()
    holds = closure.open_holds(snapshot)
    assert [(h["event"]["id"], h["event"]["kind"]) for h in holds] == [
        (100, "blocked"),
        (101, "escalated"),
    ]
    decided = copy.deepcopy(snapshot)
    decided["events"].append(
        {"id": 103, "task_id": 42, "sprint_id": 7, "kind": "decided"}
    )
    assert [h["event"]["id"] for h in closure.open_holds(decided)] == [100]
    risky = copy.deepcopy(snapshot)
    risky["sprints"][0]["tasks"][0]["status"] = "in_review"
    [risk] = closure.open_risks(risky)
    assert risk["verification_id"] == 12
    req1 = closure.requirement_outcomes(risky, risky["specification"])[0]
    assert req1["outcome"] == closure.NOT_DELIVERED and req1["why"] == closure.PLANNED


# --- the document -----------------------------------------------------------------


def test_the_report_is_placed_cited_and_compiled_in_the_pack():
    docs, _ = _docs(_snapshot())
    report = docs["final_report"]
    assert report["title"] == "Final report"
    assert (report["domain"], report["focus_area"]) == ("Governance", "Closing")
    md = report["markdown"]
    assert "PMBOK Guide, 8th edition (PMI, 2025)" in md
    assert "| Requirements delivered | 1 of 2 |" in md
    assert "| Releases cut | 1 |" in md
    assert "| Holds left open | 0 |" in md
    assert "Interim" not in md
    kinds = {s["kind"] for s in report["sources"]}
    assert {"specification", "requirement", "task", "verification"} <= kinds
    assert {"sprint", "event", "cost", "release"} <= kinds
    assert all(s.get("sha256") for s in report["sources"])
    assert report["missing"] == []
    assert report["persons"] == [
        "Acceptance by the sponsor",
        "Benefits realisation outlook",
        "Handover notes",
    ]
    assert "final_report.md" in docs["index"]["markdown"]
    marks = set(re.findall(r"S(\d+)", " ".join(re.findall(r"\\\[(S[\d, S]+)\\\]", md))))
    assert marks == {s["mark"][1:] for s in report["sources"]}


def test_each_requirement_is_delivered_or_not_with_its_tasks_and_readings():
    report = final_docs.report(_snapshot())
    table = _section(report["markdown"], "## Delivery against the requirements")
    req1 = next(line for line in table.splitlines() if line.startswith("| REQ-1 "))
    req2 = next(line for line in table.splitlines() if line.startswith("| REQ-2 "))
    assert "| delivered |" in req1 and "T-41" in req1 and "verified" in req1
    assert "not delivered: no task covered it" in req2 and "T-" not in req2
    reading = next(s for s in report["sources"] if s["kind"] == "verification")
    assert reading["id"] == "12" and reading["sha256"] == "9" * 64


def test_the_measured_outcomes_are_the_status_reports_figures():
    md = final_docs.report(_snapshot())["markdown"]
    outcomes = _section(md, "## Measured outcomes")
    assert "| Tasks done against planned | 1 of 2 |" in outcomes
    assert "| Tasks carried to a later sprint | 1 of 2 |" in outcomes
    assert "1 verified, 1 refuted, 0 unverifiable" in outcomes
    assert "0 of 1 done tasks were accepted on their first delivery" in outcomes
    assert "| Model spend | USD 0.35 (12000 reported tokens) |" in outcomes
    unknown = _snapshot()
    unknown["costs"][0].update({"cost_cents": None, "reason": "no_reported_usage"})
    md = final_docs.report(unknown)["markdown"]
    assert "not known: the provider reported no usage" in md


def test_the_releases_cut_cite_the_audit_chain():
    report = final_docs.report(_snapshot())
    table = _section(report["markdown"], "## Releases cut")
    row = next(line for line in table.splitlines() if line.startswith("| 7 "))
    assert "b" * 12 in row and "pull request" in row and "2026-11-04" in row
    release = next(s for s in report["sources"] if s["kind"] == "release")
    assert release["sha256"] == "f0" * 32
    none = _snapshot()
    none["releases"] = []
    empty = final_docs.report(none)
    assert "no release was cut" in empty["markdown"]
    assert empty["missing"] == ["releases"]


def test_holds_left_open_are_listed_with_their_events():
    report = final_docs.report(_held())
    md = report["markdown"]
    assert "| Holds left open | 2 |" in md
    table = _section(md, "## Holds and risks left open")
    assert table.count("| T-42 |") == 2
    events = {s["id"]: s["sha256"] for s in report["sources"] if s["kind"] == "event"}
    assert events["100"] == "unsigned:" + "1" * 64
    clean = final_docs.report(_snapshot())["markdown"]
    assert "nothing is left on hold" in _section(clean, "## Holds and risks left open")


def test_an_open_iteration_makes_the_report_interim():
    snapshot = _snapshot()
    snapshot["sprints"][0]["status"] = "active"
    md = final_docs.report(snapshot)["markdown"]
    assert "**Interim.**" in md and "sprint 7" in md


def test_without_records_the_report_says_what_is_missing():
    report = final_docs.report(_empty())
    assert report["missing"] == ["requirements", "sprints", "releases"]
    assert "no sprint is recorded" in report["markdown"]


def test_several_specifications_get_a_section_each():
    snapshot = _snapshot()
    other = copy.deepcopy(snapshot["specification"])
    other.update({"id": 9, "version": 1, "title": "Reports", "sha256": "12" * 32})
    other["requirements"] = [{"id": "REQ-1", "title": "Export", "criteria": []}]
    snapshot["specifications"] = [
        {**snapshot["specification"], "lineage_ids": [2, 3]},
        {**other, "lineage_ids": [9]},
    ]
    report = final_docs.report(snapshot)
    md = _section(report["markdown"], "## Delivery against the requirements")
    assert md.count("### Specification") == 2
    assert "T-41" not in md.split("Reports")[1]
    assert "specifications" in report["inputs"]
    assert closure.totals(snapshot)["requirements"] == 3


def test_lessons_are_a_pointer_to_the_register_and_no_claim_is_read():
    snapshot = _snapshot()
    report = final_docs.report(snapshot)
    assert "retrospectives" not in report["inputs"]
    assert "procedures" not in report["inputs"]
    lessons = _section(report["markdown"], "## Lessons learned")
    assert "lessons.md" in lessons
    changed = copy.deepcopy(snapshot)
    changed["retrospectives"][0]["body"] = "Everything was delivered and accepted."
    changed["procedures"] = []
    again = final_docs.report(changed)
    assert again["markdown"] == report["markdown"]
    assert again["input_sha256"] == report["input_sha256"]
    assert all(not s.get("claim") for s in report["sources"])


def test_acceptance_benefits_and_handover_are_a_persons_sections():
    plain = final_docs.report(_snapshot())
    assert plain["markdown"].count("**For a person to complete.**") == 3
    for key in ("acceptance", "benefits", "handover"):
        assert f"final_report.{key}" in statements.SECTIONS
    filled = _snapshot()
    filled[statements.key_of("final_report")] = {
        "final_report.acceptance": {
            "id": 21,
            "section": "final_report.acceptance",
            "text": "The sponsor accepted the board service on 2026-11-20.",
            "author_role": "Owner",
            "created_at": "2026-11-20T09:00:00",
            "statement_sha256": "e" * 64,
        }
    }
    report = final_docs.report(filled)
    assert "The sponsor accepted the board service" in report["markdown"]
    assert report["statements"][0]["section"] == "final_report.acceptance"
    assert report["input_sha256"] != plain["input_sha256"]
    assert any(s.get("claim") for s in report["sources"])
    for heading in ("## Summary", "## Delivery against the requirements"):
        assert _section(report["markdown"], heading) == _section(
            plain["markdown"], heading
        )


def test_the_lint_flags_the_report_when_a_record_it_read_changes():
    base = _snapshot()
    stored = _Pack(*pack.build_documents(base))
    assert pack.lint(stored, base)["ok"]

    def stale(change):
        current = copy.deepcopy(base)
        change(current)
        return {d["key"] for d in pack.lint(stored, current)["documents"] if d["stale"]}

    def reading(s):
        s["verifications"][0]["verdict"] = "refuted"

    def effort(s):
        s["effort"][0]["observed"]["rework_cycles"] = 0

    def release(s):
        s["releases"][0]["published_commit"] = "d" * 40

    def carry(s):
        s["carries"] = []

    def retro(s):
        s["retrospectives"][0]["body"] = "Another claim."

    for change in (reading, effort, release, carry):
        assert "final_report" in stale(change)
    assert "final_report" not in stale(retro)


def test_record_text_cannot_become_structure():
    snapshot = _snapshot()
    snapshot["specification"]["requirements"][0]["title"] = "| x | y |\n# Heading"
    md = final_docs.report(snapshot)["markdown"]
    assert "\n# Heading" not in md
    row = next(
        line
        for line in _section(md, "## Delivery against the requirements").splitlines()
        if line.startswith("| REQ-1 ")
    )
    assert row.count(" | ") == 5


def test_the_report_in_spanish():
    report = final_docs.report(_in("es"))
    assert report["title"] == "Informe final"
    md = report["markdown"]
    assert "| Requisitos entregados | 1 de 2 |" in md
    assert "| entregado |" in md
    assert report["persons"] == [
        "Aceptación por el patrocinador",
        "Perspectiva de realización de beneficios",
        "Notas de traspaso",
    ]
    assert final_docs.report(_in("es", _empty()))["missing"] == [
        "requisitos",
        "sprints",
        "entregas",
    ]


# --- the computed check -----------------------------------------------------------


def test_the_check_says_whether_anything_is_left_on_hold_at_closure():
    def closure_check(snapshot):
        return {r["id"]: r for r in checks.run(snapshot)}["closure"]

    met = closure_check(_snapshot())
    assert met["domain"] == "Risk" and met["result"] == "met"
    assert met["observed"] == "0 holds and 0 high-risk readings left open at closure"
    held = closure_check(_held())
    assert held["result"] == "not met"
    assert held["observed"] == "2 holds and 0 high-risk readings left open at closure"
    running = _held()
    running["sprints"][0]["status"] = "active"
    measured = closure_check(running)
    assert measured["result"] == "measured"
    assert measured["observed"] == (
        "2 holds and 0 high-risk readings open so far; iterations still open: 1"
    )
    assert closure_check(_empty())["result"] == "not computable"
