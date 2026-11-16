"""The architecture: modules, interfaces, decisions and conventions as data,
numbered and checked deterministically, its prompt section and Markdown
document, the Architect's draft read from the fixed shape, and the prompts
without an approved architecture (``services.planning.arch_check``,
``arch_author``). The routes are in ``test_architectures_api.py``, the write
to the repository in ``test_architectures_repo.py``; the shared rows in
``architecture_helpers``.
"""

import json

from architecture_helpers import (
    GOOD_ARCH,
    _approved_spec,
    _company,
    _Daemon,  # noqa: F401 - imported from here by other tests
)
from models import BacklogItem, Sprint
from models.architecture import Architecture
from services.planning import (
    arch_author,
    arch_check,
    spec_check,
    spec_planning,
)
from test_specifications import GOOD

REQS = ["REQ-1", "REQ-2"]


# --- the check ---------------------------------------------------------------


def test_normalize_numbers_everything_and_maps_names_to_ids():
    arch = arch_check.normalize(GOOD_ARCH)
    assert [m["id"] for m in arch["modules"]] == ["MOD-1", "MOD-2", "MOD-3"]
    assert arch["modules"][0]["depends_on"] == ["MOD-2"] and arch["modules"][2][
        "depends_on"
    ] == ["MOD-1"]
    assert arch["interfaces"][0] == {
        "id": "IF-1",
        "name": "Tasks HTTP API",
        "kind": "http",
        "module_id": "MOD-1",
        "contract": "GET /tasks -> [{id, title, status}]\nPATCH /tasks/{id} {status}",
        "consumers": ["MOD-3"],
    }
    assert arch["decisions"][0]["id"] == "ADR-1"
    assert arch_check.check(arch, REQS)["ok"] is True
    # A draft's own ids, a criterion id as a requirement and a self-dependency are mapped or dropped.
    again = arch_check.normalize(
        {
            "title": "x",
            "modules": [
                {
                    "id": "MOD-9",
                    "name": "A",
                    "responsibility": "r",
                    "depends_on": ["MOD-9", "B"],
                    "covers": ["REQ-1.2"],
                },
                {
                    "id": "B",
                    "name": "B",
                    "responsibility": "r",
                    "depends_on": ["MOD-9"],
                },
            ],
        }
    )
    assert [m["depends_on"] for m in again["modules"]] == [["MOD-2"], ["MOD-1"]]
    assert again["modules"][0]["covers"] == ["REQ-1"]


def test_the_check_blocks_unknown_references_cycles_and_open_points_and_warns_about_gaps():
    arch = arch_check.normalize(
        {
            "title": "x",
            "modules": [
                {
                    "name": "A",
                    "responsibility": "a",
                    "depends_on": ["B"],
                    "covers": ["REQ-1", "REQ-7"],
                },
                {"name": "B", "responsibility": "b", "depends_on": ["C"]},
                {"name": "C", "responsibility": "", "depends_on": ["A", "Ghost"]},
            ],
            "interfaces": [
                {
                    "name": "I",
                    "kind": "rpc",
                    "module": "Nope",
                    "contract": "",
                    "consumers": ["A"],
                }
            ],
            "decisions": [{"title": "Pick a store", "context": "c", "decision": ""}],
            "questions": ["Which database?"],
        }
    )
    result = arch_check.check(arch, ["REQ-1", "REQ-2"])
    codes = {(f["where"], f["code"], f["severity"]) for f in result["findings"]}
    assert ("MOD-1", "unknown_requirement", "blocking") in codes
    assert ("MOD-3", "no_responsibility", "blocking") in codes
    assert ("MOD-3", "unknown_module", "blocking") in codes
    assert ("MOD-1", "dependency_cycle", "blocking") in codes
    assert ("IF-1", "unknown_module", "blocking") in codes
    assert ("IF-1", "no_contract", "warning") in codes
    assert ("ADR-1", "no_decision", "blocking") in codes
    assert ("Q1", "open_question", "blocking") in codes
    assert ("REQ-2", "uncovered_requirement", "warning") in codes
    assert ("arch", "no_overview", "warning") in codes
    assert arch["interfaces"][0]["kind"] == "other"
    cycle = next(f for f in result["findings"] if f["code"] == "dependency_cycle")
    assert "MOD-1 → MOD-2 → MOD-3 → MOD-1" in cycle["message"]
    assert arch_check.find_cycle(arch_check.normalize(GOOD_ARCH)["modules"]) is None


def test_the_prompt_section_carries_the_modules_of_the_task_and_what_every_task_follows():
    arch = arch_check.normalize(GOOD_ARCH)
    section = arch_author.section(arch, ["REQ-2.1"])
    assert "## Architecture" in section
    assert (
        "MOD-1: Board API — `src/api/`" in section and "MOD-2: Store" in section
    )  # the dependency comes along
    assert "MOD-3: Web client" not in section
    assert "IF-1: Tasks HTTP API (http, provided by MOD-1, used by MOD-3)" in section
    assert "ADR-1: SQLite for the store" in section and "Tests live beside" in section
    rules = arch_author.section(arch, [])
    assert "ADR-1: SQLite for the store" in rules and "MOD-" not in rules
    assert "MOD-3: Web client" in arch_check.as_prompt(arch)


def test_the_markdown_document_lists_every_part():
    text = arch_check.as_markdown(arch_check.normalize(GOOD_ARCH), "Task board", 2)
    assert text.startswith("# Board service\n")
    assert "Architecture v2 for the specification *Task board*" in text
    assert "### MOD-1: Board API" in text and "- Where: `src/api/`" in text
    assert "### IF-1: Tasks HTTP API" in text and "```\nGET /tasks" in text
    assert (
        "### ADR-1: SQLite for the store" in text and "**Decision.** Use SQLite" in text
    )
    assert "## Conventions\n\n- Tests live beside" in text


# --- the Architect's draft ---------------------------------------------------


def test_the_draft_is_read_from_the_fixed_shape_and_the_prompt_carries_spec_stack_and_answers():
    assert (
        arch_author.parse_draft("```json\n" + json.dumps(GOOD_ARCH) + "\n```")[
            "modules"
        ][0]["id"]
        == "MOD-1"
    )
    assert arch_author.parse_draft("Here is a design.") is None
    assert arch_author.parse_draft(json.dumps({"title": "x", "modules": []})) is None
    existing = {
        **arch_check.normalize(GOOD_ARCH),
        "questions": [
            {"question": "Which database?", "answer": "SQLite"},
            {"question": "Open?", "answer": None},
        ],
    }
    prompt = arch_author.draft_prompt(
        "Ari",
        spec_check.normalize(GOOD),
        json.dumps({"language": "Python"}),
        "  📄 README.md",
        existing,
    )
    assert (
        "REQ-1.1: given a signed-in user" in prompt
        and "Primary language: Python" in prompt
    )
    assert (
        "README.md" in prompt
        and "Q: Which database?\n  A: SQLite" in prompt
        and "Q: Open?" not in prompt
    )
    assert "## The current draft" in prompt


def test_without_an_approved_architecture_the_prompts_carry_the_requirements_alone(
    sqlite_session,
):
    company = _company(sqlite_session)
    spec = _approved_spec(sqlite_session, company)
    sprint = Sprint(company_id=company.id, sprint_name="S", specification_id=spec.id)
    sqlite_session.add(sprint)
    sqlite_session.commit()
    item = BacklogItem(sprint_id=sprint.id, title="Move", covers=["REQ-2.1"])
    sqlite_session.add(item)
    sqlite_session.add(
        Architecture(
            company_id=company.id,
            specification_id=spec.id,
            **arch_check.normalize(GOOD_ARCH),
        )
    )
    sqlite_session.commit()
    context = spec_planning.requirements_context(sqlite_session.get_bind(), item.id)
    assert (
        "## Requirements this task covers" in context
        and "## Architecture" not in context
    )
    assert arch_author.planning_context(sqlite_session, spec.id) == ""
