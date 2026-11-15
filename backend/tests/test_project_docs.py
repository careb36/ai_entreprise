"""The documentation pack's pure builders (issue #530, ``services/planning/project_docs``):
deterministic Markdown and C4 diagrams from a snapshot, every statement with
its source, an explicit line where no record exists, the TechWriter's prose
marked as a claim, record text that cannot become structure, and the drift
lint against changed records.
"""

import copy
import re

import pytest
from services.planning.project_docs import (
    c4,
    checks,
    control_docs,
    layout,
    pack,
    refs,
    sanitize,
    scope_docs,
)

EVIL = '<script>alert(1)</script> [x](javascript:alert(1)) | "]) --> click n1 call x() %%{init}%%\n# Heading'


def _snapshot():
    spec = {
        "id": 3,
        "version": 2,
        "title": "Board service",
        "summary": "A board for a small team.",
        "status": "approved",
        "approved_at": "2026-11-01T10:00:00",
        "drafted_by_role": "PO",
        "requirements": [
            {
                "id": "REQ-1",
                "title": "List tasks",
                "description": "Show every task.",
                "criteria": [
                    {
                        "id": "REQ-1.1",
                        "given": "tasks",
                        "when": "I open the board",
                        "then": "I see them",
                    }
                ],
            },
            {"id": "REQ-2", "title": "Move tasks", "description": "", "criteria": []},
        ],
        "quality": [{"id": "NFR-1", "attribute": "Latency", "target": "under 200 ms"}],
        "constraints": ["Python 3.11"],
        "questions": [{"question": "Which browsers?", "answer": None}],
        "sha256": "ab" * 32,
    }
    arch = {
        "id": 5,
        "version": 1,
        "title": "Board architecture",
        "overview": "An API and a web client.",
        "status": "approved",
        "approved_at": "2026-11-02T10:00:00",
        "drafted_by_role": "Architect",
        "modules": [
            {
                "id": "MOD-1",
                "name": "Board API",
                "responsibility": "Serves tasks.",
                "path": "src/api/",
                "depends_on": ["MOD-2"],
                "covers": ["REQ-1"],
            },
            {
                "id": "MOD-2",
                "name": "Store",
                "responsibility": "Keeps tasks.",
                "path": "src/store/",
                "depends_on": [],
                "covers": [],
            },
            {
                "id": "MOD-3",
                "name": "Web client",
                "responsibility": "Renders.",
                "path": "web/",
                "depends_on": ["MOD-1"],
                "covers": ["REQ-1"],
            },
        ],
        "interfaces": [
            {
                "id": "IF-1",
                "name": "Tasks HTTP API",
                "kind": "http",
                "module_id": "MOD-1",
                "contract": "GET /tasks",
                "consumers": ["MOD-3"],
            },
            {
                "id": "IF-2",
                "name": "Public API",
                "kind": "http",
                "module_id": "MOD-1",
                "contract": "GET /public",
                "consumers": [],
            },
        ],
        "decisions": [
            {
                "id": "ADR-1",
                "title": "SQLite",
                "context": "",
                "decision": "Use SQLite.",
                "consequences": "",
            }
        ],
        "conventions": [],
        "sha256": "cd" * 32,
    }
    return {
        "project": {"company_id": 1, "name": "Acme"},
        "specification": spec,
        "architecture": arch,
        "stack": {
            "discovery_id": 9,
            "language": "Python",
            "frameworks": [],
            "databases": ["postgresql"],
            "infrastructure": ["docker"],
            "sha256": "ef" * 32,
        },
        "sprints": [
            {
                "id": 7,
                "name": "Sprint one",
                "goal": "List",
                "status": "completed",
                "start_date": "2026-11-03",
                "specification_id": 3,
                "team": [],
                "first_event_at": "2026-11-03T09:00:00",
                "last_event_at": "2026-11-04T09:00:00",
                "tasks": [
                    {
                        "id": 41,
                        "title": "Build list endpoint",
                        "status": "done",
                        "assignee": "Developer",
                        "priority": "high",
                        "covers": ["REQ-1.1"],
                        "kind": None,
                    },
                    {
                        "id": 42,
                        "title": "Set up CI",
                        "status": "blocked",
                        "assignee": "DevOps",
                        "priority": "medium",
                        "covers": [],
                        "kind": None,
                    },
                ],
            }
        ],
        "events": [
            {
                "id": 100,
                "task_id": 42,
                "sprint_id": 7,
                "kind": "blocked",
                "actor": "QA",
                "created_at": "2026-11-04T08:00:00",
                "chain_hash": "unsigned:" + "1" * 64,
                "reason": "Tests fail on CI.",
            },
            {
                "id": 101,
                "task_id": 42,
                "sprint_id": 7,
                "kind": "escalated",
                "actor": "system",
                "created_at": "2026-11-04T08:05:00",
                "chain_hash": "unsigned:" + "2" * 64,
                "reason": "Blocked twice.",
            },
            {
                "id": 102,
                "task_id": 42,
                "sprint_id": 7,
                "kind": "carried_over",
                "actor": "system",
                "created_at": "2026-11-04T12:00:00",
                "chain_hash": "unsigned:" + "3" * 64,
                "reason": "Carried to sprint 8.",
                "direction": "to",
            },
        ],
        "verifications": [
            {
                "verification_id": 12,
                "run_id": 30,
                "task_id": 41,
                "verdict": "verified",
                "tier": "high",
                "reasons": ["auth path src/auth.py"],
                "artifact_sha256": "9" * 64,
                "observed_at": "2026-11-03T12:00:00",
            },
            {
                "verification_id": 13,
                "run_id": 31,
                "task_id": 42,
                "verdict": "refuted",
                "tier": "medium",
                "reasons": [],
                "artifact_sha256": "8" * 64,
                "observed_at": "2026-11-04T07:00:00",
            },
        ],
        "history": [
            {
                "kind": "specification",
                "id": 2,
                "version": 1,
                "status": "superseded",
                "supersedes_id": None,
                "created_at": "2026-10-30T10:00:00",
                "approved_at": "2026-10-30T11:00:00",
                "requirement_ids": ["REQ-1"],
            },
            {
                "kind": "specification",
                "id": 3,
                "version": 2,
                "status": "approved",
                "supersedes_id": 2,
                "created_at": "2026-10-31T10:00:00",
                "approved_at": "2026-11-01T10:00:00",
                "requirement_ids": ["REQ-1", "REQ-2"],
            },
        ],
        "roles": ["Developer", "PO", "QA", "ScrumMaster"],
        "members": {"Owner": 1, "Manager": 2},
        "retrospectives": [
            {
                "id": 60,
                "sprint_id": 7,
                "created_at": "2026-11-04T10:00:00",
                "from_role": "ScrumMaster",
                "body": "1 of 2 tasks done; 1 blocked.",
            }
        ],
        "procedures": [
            {
                "id": 8,
                "title": "Run CI locally first",
                "role": "DevOps",
                "status": "active",
                "source_sprint_id": 7,
                "result": "kept",
            }
        ],
        "integrations": [
            {
                "id": 4,
                "sprint_id": 7,
                "status": "tests_passed",
                "commit": "a" * 40,
                "tested_commit": "b" * 40,
                "created_at": "2026-11-04T11:00:00",
                "tasks": [{"task_id": 41, "commit": "c" * 40}],
            }
        ],
    }


def _empty():
    return {
        "project": {"company_id": 1, "name": "Empty"},
        "specification": None,
        "architecture": None,
        "stack": None,
        "sprints": [],
        "events": [],
        "verifications": [],
        "history": [],
        "roles": [],
        "members": {},
        "retrospectives": [],
        "procedures": [],
        "integrations": [],
    }


def _docs(snapshot, prose=None):
    documents, diagrams = pack.build_documents(snapshot, prose)
    return {d["key"]: d for d in documents}, diagrams


# --- determinism and sources --------------------------------------------------


def test_the_same_records_give_the_same_bytes():
    first, first_diagrams = pack.build_documents(_snapshot())
    second, second_diagrams = pack.build_documents(copy.deepcopy(_snapshot()))
    assert first == second and first_diagrams == second_diagrams
    assert [d["key"] for d in first] == [
        "index",
        "charter",
        "scope",
        "wbs",
        "traceability",
        "schedule",
        "raci",
        "risks",
        "issues",
        "changes",
        "lessons",
        "checks",
        "c4",
    ]


def test_every_cited_mark_is_listed_with_its_record_and_digest():
    docs, _ = _docs(_snapshot())
    for key, doc in docs.items():
        if key == "index":
            continue
        marks = set(
            re.findall(
                r"S(\d+)", " ".join(re.findall(r"\\\[(S[\d, S]+)\\\]", doc["markdown"]))
            )
        )
        listed = {s["mark"][1:] for s in doc["sources"]}
        assert marks and marks == listed, key
        assert "## Sources" in doc["markdown"]
    charter = docs["charter"]
    assert charter["sources"][0] == {
        "mark": "S1",
        "kind": "specification",
        "id": "3",
        "sha256": "ab" * 32,
        "label": "version 2, approved",
    }
    risks = docs["risks"]["markdown"]
    assert "9" * 64 in risks and "touches high-risk paths" in risks
    issues = docs["issues"]["markdown"]
    assert "unsigned:" + "1" * 64 in issues
    assert "Task T-42 was blocked. Reason as recorded: Tests fail on CI." in issues
    assert "| QA |" in issues and "found the delivery of task T-42 refuted" in issues
    lessons = docs["lessons"]
    assert any(s.get("claim") for s in lessons["sources"])
    assert "a claim by the party recorded" in lessons["markdown"]
    assert "measured result: kept" in lessons["markdown"]
    assert "b" * 40 in docs["schedule"]["markdown"]


def test_the_scope_and_wbs_map_tasks_to_the_requirements_they_cover():
    docs, _ = _docs(_snapshot())
    assert "Delivery: T-41 done" in docs["scope"]["markdown"]
    assert (
        "Delivery: no task in the recorded sprints covers it."
        in docs["scope"]["markdown"]
    )
    wbs = docs["wbs"]["markdown"]
    assert (
        "### 1.1 REQ-1: List tasks" in wbs
        and "- 1.1.1 T-41: Build list endpoint" in wbs
    )
    assert (
        "### 1.3 Work not mapped to a requirement" in wbs
        and "- 1.3.1 T-42: Set up CI" in wbs
    )


def test_raci_names_roles_and_counts_people_without_naming_them():
    docs, _ = _docs(_snapshot())
    raci = docs["raci"]["markdown"]
    assert "DevOps (not hired)" in raci and "TechWriter (not hired)" in raci
    assert "Manager 2" in raci and "@" not in raci
    assert "Factory rule docs/SPRINT\\_LOOP.md" in raci


# --- missing sources and claims -----------------------------------------------


def test_without_records_every_document_says_so_instead_of_inventing():
    docs, diagrams = _docs(_empty())
    assert diagrams == []
    for key in ("charter", "scope", "schedule", "risks", "lessons", "c4"):
        assert "_No source recorded:" in docs[key]["markdown"], key
        assert docs[key]["missing"], key
    assert (
        "No source recorded: nothing in this document cites a record."
        in docs["charter"]["markdown"]
    )


def test_the_techwriters_prose_is_a_marked_claim_and_absent_prose_is_said():
    docs, _ = _docs(
        _snapshot(), prose="The team needs a shared board.\n\n# Not a heading"
    )
    charter = docs["charter"]
    assert charter["claims"] == [{"section": "background", "role": "TechWriter"}]
    assert "> **Claim.** Written by the TechWriter" in charter["markdown"]
    assert "> \\# Not a heading" in charter["markdown"]
    without, _ = _docs(_snapshot())
    assert without["charter"]["claims"] == []
    assert (
        "_No source recorded: no background was written._"
        in without["charter"]["markdown"]
    )
    # The prose never changes the facts' digest: drift is about records.
    assert charter["input_sha256"] == without["charter"]["input_sha256"]


# --- sanitization ---------------------------------------------------------------


def _evil_snapshot():
    snap = _snapshot()
    snap["specification"]["title"] = EVIL
    snap["specification"]["requirements"][0]["title"] = EVIL
    snap["architecture"]["modules"][0]["name"] = EVIL
    snap["architecture"]["interfaces"][1]["name"] = EVIL
    snap["sprints"][0]["tasks"][0]["title"] = EVIL
    snap["events"][0]["reason"] = EVIL
    snap["stack"]["databases"] = [EVIL]
    return snap


def test_record_text_cannot_become_markdown_structure():
    docs, _ = _docs(_evil_snapshot(), prose=EVIL)
    for key, doc in docs.items():
        text = doc["markdown"]
        assert "<script" not in text, key
        # A link needs an unescaped "](": every bracket from a record is escaped.
        assert not re.search(r"(?<!\\)\]\(", text), key
        assert "\n# Heading" not in text, key
    line = sanitize.md_inline(EVIL)
    assert "\n" not in line and "\\|" in line and "&lt;script&gt;" in line
    assert (
        sanitize.md_inline("see https://x.test and www.x.test")
        == "see https\\://x.test and www\\.x.test"
    )
    assert sanitize.md_paragraphs("- item\n\n12. step\n\n12 apples") == [
        "\\- item",
        "12\\. step",
        "12 apples",
    ]
    assert sanitize.md_id("REQ-1.2](x)") == "REQ-1.2x"


def test_record_text_cannot_break_out_of_a_mermaid_label():
    _, diagrams = _docs(_evil_snapshot())
    for diagram in diagrams:
        source = diagram["mermaid"]
        assert "%%" not in source and "<" not in source and "`" not in source
        for line in source.splitlines()[1:]:
            stripped = line.strip()
            assert re.match(
                r"^(subgraph g\d+\[\"|end$|n\d+|classDef |class )", stripped
            ), stripped
            assert stripped.count('"') in (0, 2, 4), stripped
            assert not stripped.startswith("click")
        assert all(re.fullmatch(r"n\d+", n["id"]) for n in diagram["nodes"])
    assert (
        sanitize.mermaid_label('a"]; click n1 call evil()') == "a click n1 call evil()"
    )


# --- C4 -----------------------------------------------------------------------------


def test_the_c4_levels_come_from_the_architecture_and_the_stack():
    _, diagrams = _docs(_snapshot())
    context, container, component = diagrams
    assert [d["level"] for d in diagrams] == ["context", "container", "component"]
    labels = {n["label"]: n["kind"] for n in context["nodes"]}
    assert labels == {
        "Board architecture": "system",
        "Users of Public API": "person",
        "postgresql": "datastore",
        "docker": "external",
    }
    names = {n["id"]: n["label"] for n in container["nodes"]}
    edges = {
        (names[e["source"]], names[e["target"]], e["label"]) for e in container["edges"]
    }
    assert ("Board API", "Store", "depends on") in edges
    assert ("Web client", "Board API", "calls Tasks HTTP API") in edges
    assert ("Users of Public API", "Board API", "uses http") in edges
    assert any(
        n["kind"] == "component" and n["label"] == "Tasks HTTP API"
        for n in component["nodes"]
    )
    assert container["mermaid"].startswith(
        'flowchart TB\n  subgraph g1["Board architecture"]'
    )
    doc = c4.document(_snapshot(), diagrams)
    assert doc["markdown"].count("```mermaid") == 3
    assert "code level is not compiled yet" in doc["markdown"]


# --- drift ------------------------------------------------------------------------------


class _Pack:
    def __init__(self, documents, diagrams):
        self.documents, self.diagrams = documents, diagrams


def test_the_lint_flags_exactly_the_documents_whose_records_changed():
    snapshot = _snapshot()
    documents, diagrams = pack.build_documents(snapshot)
    stored = _Pack(documents, diagrams)
    assert pack.lint(stored, snapshot)["ok"] is True
    changed = copy.deepcopy(snapshot)
    changed["retrospectives"].append(
        {
            "id": 61,
            "sprint_id": 7,
            "created_at": None,
            "from_role": "ScrumMaster",
            "body": "again",
        }
    )
    result = pack.lint(stored, changed)
    stale = {d["key"] for d in result["documents"] if d["stale"]}
    assert stale == {"lessons", "index", "checks"} and result["ok"] is False
    assert not any(d["stale"] for d in result["diagrams"])
    changed["architecture"]["modules"][0]["name"] = "Renamed"
    result = pack.lint(stored, changed)
    assert all(d["stale"] for d in result["diagrams"])
    assert any(
        f["where"] == "c4-context" and f["severity"] == "blocking"
        for f in result["findings"]
    )


def test_a_pack_without_diagrams_is_stale_once_an_architecture_exists():
    empty = _empty()
    stored = _Pack(*pack.build_documents(empty))
    later = copy.deepcopy(empty)
    later["architecture"] = _snapshot()["architecture"]
    assert pack.lint(stored, later)["ok"] is False


@pytest.mark.parametrize("value", ["", None, "   "])
def test_digest_and_layout_helpers_are_stable(value):
    assert refs.digest({"b": 1, "a": [1, 2]}) == refs.digest({"a": [1, 2], "b": 1})
    lines, wrote = layout.claim_block("TechWriter", value, "background")
    assert wrote is False and lines[0].startswith("_No source recorded")


def test_the_builders_read_only_their_declared_inputs():
    snapshot = _snapshot()
    for builder, inputs in (
        (scope_docs.charter, scope_docs.CHARTER_INPUTS),
        (scope_docs.scope, scope_docs.SCOPE_INPUTS),
        (scope_docs.wbs, scope_docs.WBS_INPUTS),
        (control_docs.schedule, control_docs.SCHEDULE_INPUTS),
        (control_docs.raci, control_docs.RACI_INPUTS),
        (control_docs.risks, control_docs.RISK_INPUTS),
        (control_docs.lessons, control_docs.LESSONS_INPUTS),
    ):
        trimmed = {k: (snapshot[k] if k in inputs else None) for k in snapshot}
        trimmed["project"] = snapshot["project"]
        assert builder(trimmed)["markdown"] == builder(snapshot)["markdown"], (
            builder.__name__
        )


# --- PMBOK Guide, 8th edition: placement, person sections, logs, checks ------------


def test_every_pmbok_document_names_the_edition_its_domain_and_focus_area():
    docs, _ = _docs(_snapshot())
    for key, doc in docs.items():
        if key in ("index", "c4"):
            assert doc["domain"] == ""
            continue
        assert (
            doc["domain"] in layout.DOMAINS and doc["focus_area"] in layout.FOCUS_AREAS
        ), key
        assert "PMBOK Guide, 8th edition (PMI, 2025)" in doc["markdown"], key
    assert (docs["charter"]["domain"], docs["charter"]["focus_area"]) == (
        "Governance",
        "Initiating",
    )
    index = docs["index"]["markdown"]
    assert "| Governance | Initiating | Project charter | charter.md |" in index
    assert (
        index.index("| Governance |")
        < index.index("| Scope |")
        < index.index("| Risk |")
    )


def test_decisions_only_a_person_can_take_are_templates_never_filled():
    docs, _ = _docs(_snapshot(), prose="The sponsor approved a budget of 1M.")
    charter = docs["charter"]
    assert charter["persons"] == [
        "Business justification",
        "Expected benefits",
        "Budget envelope",
        "Sponsor",
    ]
    assert charter["markdown"].count("**For a person to complete.**") == 4
    assert docs["risks"]["persons"] == ["Risk appetite and project-level risks"]
    # The prose stays a claim in its own section, never in a person's section.
    budget = charter["markdown"].split("## Budget envelope")[1].split("##")[0]
    assert "1M" not in budget


def test_the_traceability_matrix_links_requirement_task_verification_and_commit():
    docs, _ = _docs(_snapshot())
    trace = docs["traceability"]
    row = next(
        line for line in trace["markdown"].splitlines() if line.startswith("| REQ-1 |")
    )
    assert (
        "REQ-1.1" in row
        and "T-41" in row
        and "| verified |" in row
        and "cccccccccccc" in row
    )
    assert "| REQ-2 | - | none |" in trace["markdown"]
    assert trace["missing"] == ["tasks for REQ-2"]
    kinds = {s["kind"] for s in trace["sources"]}
    assert {"requirement", "task", "verification", "integration"} <= kinds


def test_the_change_log_lists_revisions_with_their_requirement_delta_and_carries():
    docs, _ = _docs(_snapshot())
    changes = docs["changes"]["markdown"]
    assert "Specification 3 version 2 revises 2 (requirements added: REQ-2)" in changes
    assert "Task T-42 of sprint 7 carried to another sprint" in changes
    assert changes.count("| C-") == 2


def test_the_check_results_compute_what_the_records_answer_and_leave_the_rest():
    results = {r["outcome"]: r for r in checks.run(_snapshot())}
    assert results["Requirements are clearly understood"]["result"] == "not met"
    assert (
        results["All the required work is planned"]["observed"]
        == "1 of 2 requirements are covered by a task"
    )
    assert results["Requirements stability is measured"]["result"] == "measured"
    assert (
        "1 requirements added and 0 removed"
        in results["Requirements stability is measured"]["observed"]
    )
    assert results["Schedule documentation is complete"]["result"] == "met"
    assert (
        results["Lessons are learned during the project, not only at its end"]["result"]
        == "met"
    )
    assert results["Work goes to the team that was formed"]["result"] == "not met"
    assert results["The risk process runs on every delivery"]["result"] == "met"
    assert results["The project delivers within its budget"]["result"] == "person"
    empty = {r["outcome"]: r["result"] for r in checks.run(_empty())}
    assert empty["Schedule documentation is complete"] == "not computable"
    doc = checks.document(_snapshot())
    assert doc["persons"] == [
        "The project delivers within its budget",
        "Stakeholders outside the company are engaged",
    ]
    assert {r["domain"] for r in checks.run(_snapshot())} == set(layout.DOMAINS)
