"""The documentation pack's ITIL transition set (issue #696,
``project_docs/transition_docs.py`` over ``project_docs/transition.py`` and
``project_docs/collect_transition.py``).

EARS criteria, each a test below:

- When a pack is compiled, the transition set shall hold a change record per
  release (a sprint integration published as a request or merged locally):
  the tasks and requirements it changed, who approved it (a company role,
  never a name or an address), its integration review, the suite result the
  daemon observed, how it was published and whether a delivery of it was
  undone, each field citing its record with a digest the backend computed.
- The release notes shall list, per release, the requirements and tasks it
  delivered, and the tasks the integration left out.
- The known errors shall list, per release, the deliveries it carries that
  the Verifier read as refuted, unverifiable or high risk at integration,
  and the holds open in its sprint when it was released; a hold resolved,
  decided or carried by the platform by then, or opened later, is not one.
- Where no release is recorded, each part shall say so instead of content.
- The handover shall stay a person's section: a template, or a person's
  statement quoted as a claim with its author's role and time, which moves
  no figure.
- The set shall read no claim (a review's words, a retrospective): changing
  one leaves its bytes and its input digest unchanged; changing a release's
  record makes the lint flag it stale.
- The set shall be written in English and Spanish.
- The snapshot shall keep only released integrations, reduce who approved
  and merged to a role, never hold an address, and keep the hold events of
  the release's sprint up to the release.
"""

import copy
import re

from project_docs_helpers import _docs, _empty, _Pack, _snapshot, _transition
from services.planning.project_docs import (
    pack,
    statements,
    transition,
    transition_docs,
)
from services.planning.project_docs.refs import digest


def _hashed(entry):
    return {**entry, "sha256": digest(entry)}


def _task(task_id, title, state, commit, run_id, verification_id, covers):
    return _hashed(
        {"task_id": task_id, "title": title, "state": state, "commit": commit}
        | {"run_id": run_id, "verification_id": verification_id, "covers": covers}
    )


def _hold(event_id, task_id, kind, at, status, digit):
    return {"id": event_id, "task_id": task_id, "kind": kind, "actor": "QA"} | {
        "created_at": f"2026-11-04T{at}:00",
        "chain_hash": "unsigned:" + digit * 64,
        "task_status_then": status,
    }


def _release():
    """The shared snapshot's release, with a task already in, one left out,
    an undecided escalation and a hold no longer blocked at release."""
    out = {k: v for k, v in _transition().items() if k != "sha256"}
    out["tasks"] += [
        _task(
            44,
            "Paginate the list",
            "already_merged",
            "e" * 40,
            32,
            14,
            ["REQ-1.2", "REQ-3"],
        ),
        _task(45, "Export", "missing", None, None, None, ["REQ-2"]),
    ]
    out["readings"] += [
        {"verification_id": 14, "run_id": 32, "task_id": 44, "verdict": "verified"}
        | {"tier": "low", "artifact_sha256": "7" * 64}
    ]
    out["reviews"] = 2
    out["hold_events"] += [
        _hold(101, 42, "escalated", "08:05", "blocked", "2"),
        _hold(103, 46, "blocked", "09:00", "in_review", "6"),
    ]
    return out


def _with(release=None, lang="en", base=None):
    out = copy.deepcopy(base or _snapshot())
    out["transition"] = [_hashed(release or _release())]
    out["language"] = lang
    return out


def _section(md, heading):
    return md.split(heading, 1)[1].split("\n## ", 1)[0]


def _row(text, start):
    return next(line for line in text.splitlines() if line.startswith(start))


# --- the analysis -----------------------------------------------------------------


def test_what_a_release_delivered_and_left_out():
    release = _release()
    assert [t["task_id"] for t in transition.delivered(release)] == [41, 44]
    assert [t["task_id"] for t in transition.left_out(release)] == [45]
    assert transition.requirements(release) == ["REQ-1", "REQ-3"]


def test_known_errors_are_problem_readings_and_holds_open_at_release():
    errors = transition.known_errors(_release())
    readings = [e for e in errors if e["kind"] == transition.READING]
    holds = [e for e in errors if e["kind"] == transition.HOLD]
    assert [(r["reading"]["task_id"], r["problem"]) for r in readings] == [
        (41, "high_risk")
    ]
    # Task 42 still blocked with an escalation undecided; 46 no longer blocked then.
    assert [(h["event"]["task_id"], h["event"]["kind"]) for h in holds] == [
        (42, "blocked"),
        (42, "escalated"),
    ]


def test_a_hold_decided_carried_or_later_is_not_a_known_error():
    release = _release()
    release["hold_events"].append(
        {
            "id": 104,
            "task_id": 42,
            "kind": "decided",
            "actor": "ScrumMaster",
            "created_at": "2026-11-04T08:30:00",
            "chain_hash": "unsigned:" + "a" * 64,
            "task_status_then": "blocked",
        }
    )
    kinds = [h["kind"] for h in transition.open_holds(release)]
    assert kinds == ["blocked"]
    release["hold_events"].append(
        {
            "id": 105,
            "task_id": 42,
            "kind": "carried_over",
            "actor": "system",
            "created_at": "2026-11-04T09:30:00",
            "chain_hash": "unsigned:" + "b" * 64,
            "task_status_then": "blocked",
        }
    )
    assert transition.open_holds(release) == []
    later = _release()
    later["released_at"] = "2026-11-04T07:00:00"
    assert transition.open_holds(later) == []


def test_a_refuted_or_unverifiable_delivery_is_a_known_error():
    release = _release()
    release["readings"][1]["verdict"] = "refuted"
    problems = {
        r["reading"]["task_id"]: r["problem"]
        for r in transition.problem_readings(release)
    }
    assert problems == {41: "high_risk", 44: "refuted"}
    release["readings"][1]["verdict"] = "unverifiable"
    assert transition.problem_readings(release)[1]["problem"] == "unverifiable"


# --- the document -----------------------------------------------------------------


def test_the_set_is_placed_cited_and_compiled_in_the_pack():
    docs, _ = _docs(_with())
    doc = docs["transition"]
    assert doc["title"] == "Transition set (ITIL 4)"
    assert (doc["domain"], doc["focus_area"]) == ("Governance", "Executing")
    md = doc["markdown"]
    assert "ITIL 4" in md and "change enablement" in md
    for heading in (
        "## Change records",
        "## Release notes",
        "## Known errors",
        "## Handover",
    ):
        assert heading in md
    kinds = {s["kind"] for s in doc["sources"]}
    assert {"integration", "task", "review", "suite", "release"} <= kinds
    assert {"verification", "event"} <= kinds
    assert all(s.get("sha256") for s in doc["sources"] if s["kind"] != "statement")
    assert doc["missing"] == []
    assert doc["persons"] == ["Handover"]
    assert "transition.md" in docs["index"]["markdown"]
    assert "ITIL 4" in docs["index"]["markdown"]
    marks = set(re.findall(r"S(\d+)", " ".join(re.findall(r"\\\[(S[\d, S]+)\\\]", md))))
    assert marks == {s["mark"][1:] for s in doc["sources"]}


def test_a_change_record_holds_every_field_with_its_source():
    doc = transition_docs.document(_with())
    record = _section(doc["markdown"], "## Change records")
    assert "### Sprint 7, integration 4" in record
    changed = _row(record, "| What changed |")
    assert "T-41" in changed and "T-44" in changed and "REQ-1, REQ-3" in changed
    approved = _row(record, "| Approved by |")
    assert "Owner" in approved and "2026-11-04" in approved
    review = _row(record, "| Integration review |")
    assert "approve" in review and "d1d1d1d1d1d1" in review and "3 files" in review
    assert "the integration's commit" in review and "2 reviews" in review
    suite = _row(record, "| Suite result |")
    assert "tests passed" in suite and "exit code 0" in suite and "b" * 12 in suite
    published = _row(record, "| Published |")
    assert "pull request" in published and "b" * 12 in published
    undone = _row(record, "| Undone |")
    assert "no undo recorded" in undone
    sources = {s["kind"]: s for s in doc["sources"]}
    assert sources["review"]["sha256"] == "e0" * 32
    assert sources["release"]["sha256"] == "f0" * 32
    assert sources["integration"]["sha256"] == "ab" * 32
    assert sources["suite"]["sha256"] == _release()["suite"]["sha256"]
    assert "@" not in doc["markdown"]


def test_an_undone_delivery_and_a_local_merge_are_recorded():
    release = _release()
    release["undone"] = [
        {
            "run_id": 30,
            "task_id": 41,
            "audit_log_id": 80,
            "chain_hash": "c0" * 32,
            "at": "2026-11-05T10:00:00",
        }
    ]
    release["publications"] = []
    release["merge"] = _hashed(
        {
            "base": "main",
            "commit": "a" * 40,
            "before": "1" * 40,
            "after": "2" * 40,
            "fast_forward": True,
            "already": False,
            "by": "former",
            "review_id": 9,
            "audit_log_id": 79,
            "chain_hash": "b0" * 32,
            "at": "2026-11-04T12:00:00",
        }
    )
    doc = transition_docs.document(_with(release))
    record = _section(doc["markdown"], "## Change records")
    undone = _row(record, "| Undone |")
    assert "T-41" in undone and "2026-11-05" in undone
    published = _row(record, "| Published |")
    assert "merged locally into main" in published and "fast-forward" in published
    assert "an account no longer in the company" in published
    kinds = {s["kind"]: s for s in doc["sources"]}
    assert kinds["undo"]["sha256"] == "c0" * 32
    assert kinds["merge"]["sha256"] == "b0" * 32


def test_without_a_review_or_suite_the_record_says_so():
    release = _release()
    release["review"], release["reviews"] = None, 0
    release["suite"] = _hashed(
        {**release["suite"], "status": "untested", "exit_code": None}
    )
    record = _section(
        transition_docs.document(_with(release))["markdown"], "## Change records"
    )
    assert "no person's review is recorded" in _row(record, "| Approved by |")
    assert "untested" in _row(record, "| Suite result |")


def test_the_release_notes_list_delivered_requirements_and_tasks():
    notes = _section(transition_docs.document(_with())["markdown"], "## Release notes")
    assert "### Sprint 7, integration 4" in notes
    assert "REQ-1, REQ-3" in notes
    assert "Build list endpoint" in _row(notes, "| T-41 |")
    assert "Paginate the list" in _row(notes, "| T-44 |")
    left = _row(notes, "| T-45 |")
    assert "not in this release" in left and "no branch" in left


def test_the_known_errors_cite_the_verifier_and_the_events():
    doc = transition_docs.document(_with())
    errors = _section(doc["markdown"], "## Known errors")
    reading = _row(errors, "| 7 | T-41 |")
    assert "high risk" in reading
    blocked = [line for line in errors.splitlines() if line.startswith("| 7 | T-42 |")]
    assert len(blocked) == 2
    assert "T-46" not in errors
    event = next(s for s in doc["sources"] if s["kind"] == "event" and s["id"] == "100")
    assert event["sha256"] == "unsigned:" + "1" * 64
    clean = _release()
    clean["readings"][0]["tier"] = "low"
    clean["hold_events"] = []
    none = transition_docs.document(_with(clean))
    assert "No known error" in _section(none["markdown"], "## Known errors")


def test_without_releases_each_part_says_what_is_missing():
    doc = transition_docs.document(_empty())
    assert doc["missing"] == ["releases"]
    md = doc["markdown"]
    assert md.count("_No source recorded:") >= 3
    assert "**For a person to complete.**" in md


def test_the_handover_is_a_persons_section():
    plain = transition_docs.document(_with())
    assert "**For a person to complete.**" in _section(plain["markdown"], "## Handover")
    assert "transition.handover" in statements.SECTIONS
    filled = _with()
    filled[statements.key_of("transition")] = {
        "transition.handover": {
            "id": 21,
            "section": "transition.handover",
            "text": "Operations takes release 4 over on Monday.",
            "author_role": "Manager",
            "created_at": "2026-11-18T09:00:00",
            "statement_sha256": "e" * 64,
        }
    }
    doc = transition_docs.document(filled)
    handover = _section(doc["markdown"], "## Handover")
    assert "Operations takes release 4 over on Monday." in handover
    assert "Manager" in handover and "2026-11-18 09:00 UTC" in handover
    assert doc["statements"][0]["section"] == "transition.handover"
    assert any(s.get("claim") for s in doc["sources"])
    assert doc["input_sha256"] != plain["input_sha256"]
    for heading in ("## Change records", "## Release notes", "## Known errors"):
        assert _section(doc["markdown"], heading) == _section(
            plain["markdown"], heading
        )


def test_the_set_reads_no_claim():
    snapshot = _with()
    doc = transition_docs.document(snapshot)
    assert set(doc["inputs"]) == {"transition", statements.key_of("transition")}
    changed = copy.deepcopy(snapshot)
    changed["retrospectives"][0]["body"] = "Release 4 had no problem at all."
    again = transition_docs.document(changed)
    assert again["markdown"] == doc["markdown"]
    assert again["input_sha256"] == doc["input_sha256"]
    assert all(not s.get("claim") for s in doc["sources"])


def test_the_lint_flags_the_set_when_a_release_record_changes():
    base = _with()
    stored = _Pack(*pack.build_documents(base))
    assert pack.lint(stored, base)["ok"]

    def stale(change):
        current = copy.deepcopy(base)
        change(current)
        return {d["key"] for d in pack.lint(stored, current)["documents"] if d["stale"]}

    def undo(s):
        s["transition"][0]["undone"] = [{"run_id": 30, "task_id": 41}]

    def retro(s):
        s["retrospectives"][0]["body"] = "Another claim."

    assert "transition" in stale(undo)
    assert "transition" not in stale(retro)


def test_record_text_cannot_become_structure():
    release = _release()
    release["tasks"][0]["title"] = "| injected | row |\n# Heading"
    md = transition_docs.document(_with(release))["markdown"]
    assert "\n# Heading" not in md
    assert _row(_section(md, "## Release notes"), "| T-41 |").count(" | ") == 4


def test_the_set_in_spanish():
    doc = transition_docs.document(_with(lang="es"))
    assert doc["title"] == "Conjunto de transición (ITIL 4)"
    md = doc["markdown"]
    for heading in (
        "## Registros de cambio",
        "## Notas de la entrega",
        "## Errores conocidos",
        "## Traspaso",
    ):
        assert heading in md
    assert doc["persons"] == ["Traspaso"]
    empty = copy.deepcopy(_empty())
    empty["language"] = "es"
    assert transition_docs.document(empty)["missing"] == ["entregas"]


# --- the snapshot -----------------------------------------------------------------


def test_the_snapshot_keeps_released_integrations_by_role(sqlite_session):
    import secrets
    from datetime import datetime

    from models import (
        AgentRun,
        AuditLog,
        BacklogItem,
        Company,
        CompanyMember,
        IntegrationReview,
        Sprint,
        SprintIntegration,
        Verification,
    )
    from models.company import User
    from services.planning.project_docs import collect
    from services.tasks import task_events

    company = Company(company_name=f"ITIL {secrets.token_hex(3)}", autonomy_level=2)
    sqlite_session.add(company)
    sqlite_session.commit()
    sprint = Sprint(company_id=company.id, sprint_name="One", status="completed")
    sqlite_session.add(sprint)
    sqlite_session.commit()
    done = BacklogItem(
        sprint_id=sprint.id, title="Build", status="done", covers=["REQ-1.1"]
    )
    held = BacklogItem(sprint_id=sprint.id, title="CI", status="blocked")
    sqlite_session.add_all([done, held])
    sqlite_session.commit()
    task_events.record(
        sqlite_session,
        held,
        "blocked",
        "QA",
        status="blocked",
        payload={"reason": "red"},
    )
    sqlite_session.commit()
    run = AgentRun(
        company_id=company.id,
        external_ref=f"itil:{secrets.token_hex(6)}",
        producer="test",
        artifact_digest="9" * 64,
        artifact_size=1,
    )
    sqlite_session.add(run)
    sqlite_session.commit()
    reading = Verification(
        company_id=company.id,
        agent_run_id=run.id,
        verdict="refuted",
        checks=[{"name": "risk_tier", "tier": "medium"}],
    )
    address = "owner" + "@example.test"
    owner = User(email=address, full_name="Olga Owner", password_hash="x")
    sqlite_session.add_all([reading, owner])
    sqlite_session.commit()
    sqlite_session.add(
        CompanyMember(company_id=company.id, user_id=owner.id, human_role="Owner")
    )
    published = AuditLog(
        company_id=company.id, action="sprint_published", chain_hash="0a" * 32
    )
    undone = AuditLog(
        company_id=company.id,
        action="run_delivery_undone",
        details={"agent_run_id": run.id},
        chain_hash="0b" * 32,
    )
    sqlite_session.add_all([published, undone])
    sqlite_session.commit()
    row = SprintIntegration(
        company_id=company.id,
        sprint_id=sprint.id,
        trigger="sprint_closed",
        requested_by="system",
        status="tests_failed",
        branch="fabrique/sprint-1",
        base="main",
        commit="a" * 40,
        tested_commit="a" * 40,
        test_run={"command": "pytest", "exit_code": 1, "failing_tests": ["t1", "t2"]},
        tasks=[
            {
                "task_id": done.id,
                "title": "Build",
                "state": "merged",
                "merge_commit": "c" * 40,
                "run_id": run.id,
                "verification_id": reading.id,
            }
        ],
        publication={
            "commit": "a" * 40,
            "how": "gh",
            "provider": "github",
            "base": "main",
            "url": "https://example.test/pull/1",
            "published_by": address,
            "audit_log_id": published.id,
        },
        details={
            "local_merge": {
                "commit": "a" * 40,
                "base": "main",
                "before": "1" * 40,
                "after": "2" * 40,
                "merged_by": address,
                "merged_by_user_id": owner.id,
                "merged_at": "2099-01-01T00:00:00+00:00",
            }
        },
    )
    never = SprintIntegration(
        company_id=company.id,
        sprint_id=sprint.id,
        trigger="manual",
        requested_by="system",
        status="tests_passed",
        branch="fabrique/sprint-1",
        base="main",
    )
    sqlite_session.add_all([row, never])
    sqlite_session.commit()
    sqlite_session.add(
        IntegrationReview(
            company_id=company.id,
            sprint_id=sprint.id,
            integration_id=row.id,
            event="approve",
            body="Looks good to " + address,
            outcome="approved",
            commit="a" * 40,
            diff_sha256="d" * 64,
            files=2,
            author_name=address,
            author_user_id=owner.id,
            created_at=datetime(2026, 11, 4, 11, 0),
        )
    )
    sqlite_session.commit()

    snap = collect.snapshot(sqlite_session, company)
    [entry] = snap["transition"]
    assert entry["integration_id"] == row.id and entry["sha256"]
    assert entry["review"]["by"] == "Owner" and entry["review"]["on_commit"]
    assert entry["review"]["diff_sha256"] == "d" * 64
    assert entry["merge"]["by"] == "Owner"
    assert entry["publications"][0]["chain_hash"] == "0a" * 32
    assert entry["suite"]["exit_code"] == 1 and entry["suite"]["failing"] == 2
    assert entry["tasks"][0]["covers"] == ["REQ-1.1"]
    assert entry["readings"][0]["verdict"] == "refuted"
    assert entry["readings"][0]["artifact_sha256"] == "9" * 64
    assert entry["undone"][0]["chain_hash"] == "0b" * 32
    [hold] = entry["hold_events"]
    assert hold["task_id"] == held.id and hold["task_status_then"] == "blocked"
    assert address not in repr(snap) and "Looks good" not in repr(snap)
    assert "example.test/pull" not in repr(snap)
    assert snap[statements.key_of("transition")] == {}
