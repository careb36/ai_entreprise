"""The documentation pack's pure builders (issue #530, ``services/planning/project_docs``):
deterministic Markdown from a snapshot, every statement with its source, an
explicit line where no record exists, the TechWriter's prose marked as a
claim, and record text that cannot become structure. The C4 diagrams and the
drift lint are in ``test_project_docs_diagrams.py``, the PMBOK documents in
``test_project_docs_pmbok.py``; the shared snapshot in
``project_docs_helpers``.
"""

import copy
import re

from project_docs_helpers import (
    _docs,
    _empty,
    _Pack,  # noqa: F401 - imported from here by other tests
    _snapshot,
)
from services.planning.project_docs import (
    pack,
    sanitize,
)

EVIL = '<script>alert(1)</script> [x](javascript:alert(1)) | "]) --> click n1 call x() %%{init}%%\n# Heading'


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
        "wstg_checklist",
        "security_report",
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
