"""The documentation pack's PMBOK Guide documents (issue #530, 8th edition):
each names the edition, its domain and focus area; the decisions only a
person can take stay templates; the traceability matrix links requirement,
task, verification and commit; the change log lists revisions; the check
results compute what the records answer and leave the rest. The shared
snapshot is in ``project_docs_helpers``.
"""

from project_docs_helpers import _docs, _empty, _snapshot
from services.planning.project_docs import (
    checks,
    layout,
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
