"""The documentation pack's C4 diagrams and drift lint (issue #530,
``services/planning/project_docs``): the C4 levels come from the
architecture and the stack, the lint flags exactly the documents whose
records changed (and a pack without diagrams once an architecture exists),
the digest and layout helpers are stable and the builders read only their
declared inputs. The shared snapshot is in ``project_docs_helpers``.
"""

import copy

import pytest
from project_docs_helpers import _docs, _empty, _Pack, _snapshot
from services.planning.project_docs import (
    c4,
    control_docs,
    layout,
    pack,
    refs,
    scope_docs,
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
    assert "not observed: no code graph" in doc["markdown"]


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
    lines, wrote = layout.claim_block("en", "TechWriter", value, "background")
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
