"""The documentation pack's release and iteration plan (issue #530, phase 2,
``project_docs/release_docs.py`` over ``project_docs/iterations.py``).

EARS criteria, each a test below:

- When a pack is compiled, the plan shall list each iteration with the
  specification it was planned from, its goal as planned, its scope, its team
  and who chose it, its tasks done against planned and the requirements its
  tasks carry, each with the record it came from.
- The plan shall map every requirement of the documented specifications to
  the iterations and tasks that carry it, and say whether it is delivered
  (a covering task is done), planned or not planned.
- The plan shall list the carry-over between iterations from the carries the
  platform recorded, each cited with the task event's chain hash.
- The plan shall list the releases cut (a sprint integration published as
  one request) with the daemon's commit, cited with the audit row's chain
  hash; where none was cut, it shall say so instead of a release.
- Target release dates and scope decisions ahead shall stay a person's
  section (a template, or a person's statement quoted as a claim), and a
  statement shall never move a figure.
- The plan shall not read a retrospective or any other claim: changing one
  leaves its bytes and its input digest unchanged.
- When a record it read changes (a carry, a release, a sprint's plan), the
  lint shall flag the plan as stale.
- The check results shall measure the requirements delivered and the
  completed iterations released.
- The snapshot shall keep only carries the platform wrote, and published
  integrations without the name of the person who published them.
"""

import copy
import re

from project_docs_helpers import _docs, _empty, _Pack, _snapshot
from services.planning.project_docs import (
    checks,
    iterations,
    pack,
    release_docs,
    statements,
)


def _in(lang, snapshot=None):
    out = copy.deepcopy(snapshot or _snapshot())
    out["language"] = lang
    return out


def _section(md, heading):
    return md.split(heading, 1)[1].split("\n## ", 1)[0]


# --- the figures ------------------------------------------------------------------


def test_the_figures_come_from_the_records():
    totals = iterations.totals(_snapshot())
    assert (totals["iterations"], totals["completed"], totals["open"]) == (1, 1, 0)
    assert (totals["requirements"], totals["planned"], totals["delivered"]) == (
        2,
        1,
        1,
    )
    assert (totals["carries"], totals["releases"]) == (1, 1)
    assert totals["completed_released"] == 1
    [req1, req2] = iterations.requirement_rows(
        _snapshot(), _snapshot()["specification"]
    )
    assert req1["id"] == "REQ-1" and req1["state"] == iterations.DELIVERED
    assert [t["id"] for _, t in req1["tasks"]] == [41]
    assert req2["state"] == iterations.NOT_PLANNED and req2["tasks"] == []
    assert iterations.requirement_of("REQ-1.2") == "REQ-1"
    assert iterations.requirement_of("NFR-3") == "NFR-3"
    sprint = _snapshot()["sprints"][0]
    assert iterations.carried_requirements(sprint) == ["REQ-1"]


def test_a_requirement_whose_task_is_not_done_is_planned():
    snapshot = _snapshot()
    snapshot["sprints"][0]["tasks"][0]["status"] = "in_review"
    [req1, _] = iterations.requirement_rows(snapshot, snapshot["specification"])
    assert req1["state"] == iterations.PLANNED
    assert iterations.totals(snapshot)["delivered"] == 0


# --- the document -----------------------------------------------------------------


def test_the_plan_is_placed_cited_and_compiled_in_the_pack():
    docs, _ = _docs(_snapshot())
    plan = docs["release_plan"]
    assert plan["title"] == "Release and iteration plan"
    assert (plan["domain"], plan["focus_area"]) == ("Schedule", "Planning")
    md = plan["markdown"]
    assert "PMBOK Guide, 8th edition (PMI, 2025)" in md
    assert "| Requirements carried by an iteration | 1 of 2 |" in md
    assert "| Requirements delivered | 1 of 2 |" in md
    assert "| Releases cut | 1 |" in md
    iteration = _section(md, "## Iteration plan")
    row = next(line for line in iteration.splitlines() if line.startswith("| 7 "))
    assert "specification 3, version 2" in row and "| List |" in row
    assert "| M |" in row and "suggested by the PO, confirmed by a person" in row
    assert "| 1/2 |" in row and "REQ-1" in row
    kinds = {s["kind"] for s in plan["sources"]}
    assert {"sprint", "specification", "requirement", "task", "event"} <= kinds
    assert "release" in kinds
    assert plan["missing"] == []
    assert plan["persons"] == ["Target release dates and scope ahead"]
    assert "release_plan.md" in docs["index"]["markdown"]
    marks = set(re.findall(r"S(\d+)", " ".join(re.findall(r"\\\[(S[\d, S]+)\\\]", md))))
    assert marks == {s["mark"][1:] for s in plan["sources"]}


def test_each_requirement_is_mapped_to_its_iterations():
    md = release_docs.plan(_snapshot())["markdown"]
    table = _section(md, "## Requirements by iteration")
    req1 = next(line for line in table.splitlines() if line.startswith("| REQ-1 "))
    req2 = next(line for line in table.splitlines() if line.startswith("| REQ-2 "))
    assert "| 7 |" in req1 and "T-41" in req1 and "| delivered |" in req1
    assert "| not planned |" in req2 and "T-" not in req2


def test_carry_over_cites_the_task_events_chain_hash():
    plan = release_docs.plan(_snapshot())
    table = _section(plan["markdown"], "## Carry-over between iterations")
    row = next(line for line in table.splitlines() if line.startswith("| 7 "))
    assert "T-42" in row and "| 8 |" in row and "T-43" in row
    assert "2026-11-04" in row
    event = next(s for s in plan["sources"] if s["kind"] == "event")
    assert event["id"] == "102" and event["sha256"] == "unsigned:" + "3" * 64
    none = copy.deepcopy(_snapshot())
    none["carries"] = []
    assert "no task was carried" in release_docs.plan(none)["markdown"]
    assert release_docs.plan(none)["missing"] == []


def test_a_release_cites_the_daemons_commit_and_the_audit_chain():
    plan = release_docs.plan(_snapshot())
    table = _section(plan["markdown"], "## Releases")
    row = next(line for line in table.splitlines() if line.startswith("| 7 "))
    assert "b" * 12 in row and "pull request" in row and "2026-11-04" in row
    release = next(s for s in plan["sources"] if s["kind"] == "release")
    assert release["id"] == "4" and release["sha256"] == "f0" * 32
    none = copy.deepcopy(_snapshot())
    none["releases"] = []
    empty = release_docs.plan(none)
    assert "no release has been cut" in empty["markdown"]
    assert empty["missing"] == ["releases"]


def test_without_records_the_plan_says_what_is_missing():
    plan = release_docs.plan(_empty())
    assert plan["missing"] == ["iterations", "requirements", "releases"]
    assert "no sprint is recorded" in plan["markdown"]


def test_several_specifications_get_a_section_each():
    snapshot = _snapshot()
    other = copy.deepcopy(snapshot["specification"])
    other.update({"id": 9, "version": 1, "title": "Reports", "sha256": "12" * 32})
    other["requirements"] = [{"id": "REQ-1", "title": "Export", "criteria": []}]
    snapshot["specifications"] = [
        {**snapshot["specification"], "lineage_ids": [2, 3]},
        {**other, "lineage_ids": [9]},
    ]
    plan = release_docs.plan(snapshot)
    md = _section(plan["markdown"], "## Requirements by iteration")
    assert md.count("### Specification") == 2
    reports = md.split("Reports")[1]
    assert "| not planned |" in reports and "T-41" not in reports
    assert "specifications" in plan["inputs"]
    assert iterations.totals(snapshot)["requirements"] == 3


def test_target_dates_and_scope_ahead_are_a_persons_section():
    plain = release_docs.plan(_snapshot())
    assert "**For a person to complete.**" in plain["markdown"]
    assert "release_plan.targets" in statements.SECTIONS
    filled = copy.deepcopy(_snapshot())
    filled[statements.key_of("release_plan")] = {
        "release_plan.targets": {
            "id": 11,
            "section": "release_plan.targets",
            "text": "Release 1.0 on 2027-01-15 with REQ-2.",
            "author_role": "Manager",
            "created_at": "2026-11-18T09:00:00",
            "statement_sha256": "e" * 64,
        }
    }
    plan = release_docs.plan(filled)
    assert "Release 1.0 on 2027-01-15 with REQ-2." in plan["markdown"]
    assert plan["statements"][0]["section"] == "release_plan.targets"
    assert plan["input_sha256"] != plain["input_sha256"]
    assert any(s.get("claim") for s in plan["sources"])
    for heading in ("## Summary", "## Requirements by iteration"):
        assert _section(plan["markdown"], heading) == _section(
            plain["markdown"], heading
        )


def test_the_plan_reads_no_claim():
    snapshot = _snapshot()
    plan = release_docs.plan(snapshot)
    assert "retrospectives" not in plan["inputs"]
    changed = copy.deepcopy(snapshot)
    changed["retrospectives"][0]["body"] = "Release 2 ships next week."
    changed["procedures"] = []
    again = release_docs.plan(changed)
    assert again["markdown"] == plan["markdown"]
    assert again["input_sha256"] == plan["input_sha256"]
    assert all(not s.get("claim") for s in plan["sources"])


def test_the_lint_flags_the_plan_when_a_record_it_read_changes():
    base = _snapshot()
    stored = _Pack(*pack.build_documents(base))
    assert pack.lint(stored, base)["ok"]

    def stale(change):
        current = copy.deepcopy(base)
        change(current)
        return {d["key"] for d in pack.lint(stored, current)["documents"] if d["stale"]}

    def carry(s):
        s["carries"][0]["to_sprint_id"] = 9

    def release(s):
        s["releases"][0]["published_commit"] = "d" * 40

    def scope(s):
        s["iteration_plans"][0]["scope_size"] = "L"

    def retro(s):
        s["retrospectives"][0]["body"] = "Another claim."

    assert "release_plan" in stale(carry)
    assert "release_plan" in stale(release)
    assert "release_plan" in stale(scope)
    assert "release_plan" not in stale(retro)


def test_record_text_cannot_become_structure():
    snapshot = _snapshot()
    snapshot["sprints"][0]["goal"] = "| injected | row |\n# Heading"
    snapshot["iteration_plans"][0]["team_source"] = "<b>x</b>|y"
    md = release_docs.plan(snapshot)["markdown"]
    assert "\n# Heading" not in md
    row = next(
        line
        for line in _section(md, "## Iteration plan").splitlines()
        if line.startswith("| 7 ")
    )
    assert row.count(" | ") == 9


def test_the_plan_in_spanish():
    plan = release_docs.plan(_in("es"))
    assert plan["title"] == "Plan de entregas e iteraciones"
    md = plan["markdown"]
    assert "| Requisitos entregados | 1 de 2 |" in md
    assert "| entregado |" in md
    assert plan["persons"] == ["Fechas objetivo de entrega y alcance por delante"]
    assert release_docs.plan(_in("es", _empty()))["missing"] == [
        "iteraciones",
        "requisitos",
        "entregas",
    ]


# --- the computed checks ----------------------------------------------------------


def test_the_checks_measure_delivery_and_releases():
    results = {r["id"]: r for r in checks.run(_snapshot())}
    assert results["delivered"]["domain"] == "Scope"
    assert results["delivered"]["result"] == "measured"
    assert results["delivered"]["observed"] == (
        "1 of 2 requirements are delivered by a done task"
    )
    assert results["released"]["domain"] == "Schedule"
    assert results["released"]["result"] == "measured"
    assert results["released"]["observed"] == (
        "1 of 1 completed sprints were published as a release"
    )
    empty = {r["id"]: r["result"] for r in checks.run(_empty())}
    assert empty["delivered"] == empty["released"] == "not computable"


# --- the snapshot -----------------------------------------------------------------


def test_the_snapshot_keeps_the_platforms_carries_and_published_releases(
    sqlite_session,
):
    import secrets

    from models import AuditLog, BacklogItem, Company, Sprint, SprintIntegration
    from services.planning.project_docs import collect
    from services.tasks import task_events

    company = Company(company_name=f"Plan {secrets.token_hex(3)}", autonomy_level=2)
    sqlite_session.add(company)
    sqlite_session.commit()
    one = Sprint(
        company_id=company.id, sprint_name="One", status="completed", scope_size="S"
    )
    two = Sprint(company_id=company.id, sprint_name="Two", status="active")
    sqlite_session.add_all([one, two])
    sqlite_session.commit()
    old = BacklogItem(sprint_id=one.id, title="Build", status="blocked")
    new = BacklogItem(sprint_id=two.id, title="Build", status="todo")
    sqlite_session.add_all([old, new])
    sqlite_session.commit()

    def carry(actor):
        task_events.record(
            sqlite_session,
            old,
            "carried_over",
            actor,
            payload={"direction": "to", "task": new.id, "sprint": two.id},
        )
        sqlite_session.commit()

    carry(task_events.SYSTEM)
    carry("ScrumMaster")  # an agent's account of a carry: a claim, not read
    audit = AuditLog(
        company_id=company.id,
        action="sprint_published",
        chain_hash="0a" * 32,
    )
    sqlite_session.add(audit)
    sqlite_session.commit()
    publisher = "someone" + "@example.test"
    published = SprintIntegration(
        company_id=company.id,
        sprint_id=one.id,
        trigger="sprint_closed",
        requested_by="system",
        status="tests_passed",
        branch="fabrique/sprint-1",
        base="main",
        tested_commit="1" * 40,
        publication={
            "provider": "github",
            "branch": "fabrique/sprint-1",
            "base": "main",
            "commit": "2" * 40,
            "url": "https://example.test/pull/1",
            "how": "gh",
            "updated": False,
            "published_by": publisher,
            "audit_log_id": audit.id,
        },
    )
    unpublished = SprintIntegration(
        company_id=company.id,
        sprint_id=two.id,
        trigger="manual",
        requested_by="system",
        status="tests_failed",
        branch="fabrique/sprint-2",
        base="main",
    )
    sqlite_session.add_all([published, unpublished])
    sqlite_session.commit()

    snap = collect.snapshot(sqlite_session, company)
    [entry] = snap["carries"]
    assert (entry["from_task_id"], entry["from_sprint_id"]) == (old.id, one.id)
    assert (entry["to_task_id"], entry["to_sprint_id"]) == (new.id, two.id)
    assert entry["chain_hash"]
    [release] = snap["releases"]
    assert release["sprint_id"] == one.id and release["published_commit"] == "2" * 40
    assert release["audit_chain_hash"] == "0a" * 32 and release["published_at"]
    assert publisher not in repr(snap)
    plans = {p["sprint_id"]: p for p in snap["iteration_plans"]}
    assert plans[one.id]["scope_size"] == "S"
    assert snap[statements.key_of("release_plan")] == {}
