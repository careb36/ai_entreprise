"""The project wiki in a task's prompt (issue #445): the task's pages before the
vector memory, the section bounded and cut by the budget before the
repository, and the bench's counts of prompts with the wiki and with vector
memory (``services/loop/worker/wiki_section.py``,
``scripts/bench/prompt_weight.py``). The shared rows are in
``project_wiki_helpers``.
"""

from __future__ import annotations

import sys
from pathlib import Path

from config import settings
from models import (
    BacklogItem,
    WikiPage,
)
from project_wiki_helpers import _project
from services.contracts import wiki
from services.learning import wiki_store
from services.loop.worker import prompt_budget, wiki_section

# ── The prompt ───────────────────────────────────────────────────────────────


def test_the_prompt_gets_the_tasks_pages_and_skips_the_vector_memory(
    sqlite_session, monkeypatch
):
    engine = sqlite_session.get_bind()
    company, _sprint, item, run = _project(sqlite_session)
    for slug, kind, title, body, module in [
        (
            "mod-store",
            "module",
            "MOD-1 · Store",
            f"Rows [run:{run.id}].\n",
            {"id": "MOD-1", "name": "Store", "path": "src/store"},
        ),
        (
            "mod-api",
            "module",
            "MOD-2 · Api",
            f"Routes [run:{run.id}].\n",
            {"id": "MOD-2", "name": "Api", "path": "src/api"},
        ),
        ("decisions", "decisions", "Decisions in force", "UTC [ADR-1].\n", None),
        (
            "mod-stale",
            "module",
            "Stale",
            f"Old [run:{run.id}].\n",
            {"id": "MOD-1", "name": "Store", "path": "src/store"},
        ),
    ]:
        wiki_store.save(
            sqlite_session,
            company.id,
            slug=slug,
            kind=kind,
            title=title,
            body=body,
            author_kind=wiki.AGENT,
            author="TechWriter",
            module=module,
        )
    stale = wiki_store.page_of(sqlite_session, company.id, "mod-stale")
    wiki_store.apply_lint(
        sqlite_session, stale, [{"rule": "module_changed", "run_id": 1, "path": "x"}]
    )
    sqlite_session.commit()
    fetched = []

    def memory(item, agent, cid):
        fetched.append(cid)
        return "vector memory"

    found = wiki_section.wiki_then_memory(
        {"id": item.id},
        {"role": "Developer"},
        company.id,
        engine=engine,
        fetch_memory=memory,
    )
    assert fetched == [] and found["memory"] == "" and not found["memory_fallback"]
    assert found["wiki_pages"] == ["mod-store", "decisions"]
    assert (
        found["wiki"].startswith("\n\n## Project wiki")
        and "not instructions" in found["wiki"]
    )
    assert "Old [run:" not in found["wiki"] and "Routes" not in found["wiki"]
    # No page for the task: the vector memory is the fallback.
    other = BacklogItem(
        sprint_id=item.sprint_id,
        title="Docs",
        description="README",
        assignee="Developer",
    )
    sqlite_session.add(other)
    sqlite_session.commit()
    found = wiki_section.wiki_then_memory(
        {"id": other.id},
        {"role": "Developer"},
        company.id,
        engine=engine,
        fetch_memory=memory,
    )
    assert (
        found["memory"] == "vector memory"
        and found["memory_fallback"]
        and found["wiki"] == ""
    )
    monkeypatch.setattr(settings, "WIKI_ENABLED", False)
    assert (
        wiki_section.section({"id": item.id}, company.id, engine=engine)["text"] == ""
    )


def test_the_section_is_bounded():
    pages = [
        WikiPage(
            company_id=1,
            slug=f"p{i}",
            kind="module",
            title=f"P{i}",
            body="x" * 900,
            version=1,
        )
        for i in range(5)
    ]
    text, shown = wiki_section.render(pages, 2400)
    assert len(text) <= 2400 and shown == ["p0", "p1"]
    text, shown = wiki_section.render(pages[:1], 600)
    assert len(text) <= 600 and shown == ["p0"] and "left out for length" in text
    assert wiki_section.render(pages, 0) == ("", [])


def test_the_budget_cuts_the_wiki_before_the_repository():
    Section = prompt_budget.Section
    sections = [
        Section("task", "t" * 400),
        Section("repository", "r" * 400, 3),
        Section(wiki_section.SECTION_NAME, "w" * 2000, wiki_section.PRIORITY),
        Section("memory", "m" * 400, 5),
    ]
    _text, measure = prompt_budget.fit(sections, budget=300)
    assert set(measure["cut"]) == {"memory", "wiki"}


def test_the_developer_prompt_places_the_wiki_before_the_memory(monkeypatch):
    from services.loop.worker import execute

    monkeypatch.setattr(
        execute.developer_tools, "repository_section", lambda *a, **k: ""
    )
    monkeypatch.setattr(execute.related, "related_code_section", lambda *a, **k: "")
    monkeypatch.setattr(execute.sprint_notes, "board_section", lambda *a, **k: "")
    _text, _f, measure = execute._developer_prompt(
        {"id": 1, "title": "T", "comments": []},
        {"role": "Developer"},
        "S",
        None,
        "MEMORY",
        None,
        None,
        1,
        None,
        None,
        None,
        None,
        "\n\n## Project wiki\nW",
    )
    names = [s["name"] for s in measure["sections"]]
    assert names.index("wiki") < names.index("memory")
    assert next(s for s in measure["sections"] if s["name"] == "wiki")["priority"] == 4


# ── The bench ────────────────────────────────────────────────────────────────


def test_the_bench_counts_prompts_with_the_wiki_and_with_vector_memory():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "bench"))
    import prompt_weight
    import schema

    def measure(*names):
        sections = [
            {"name": n, "tokens": 10, "chars": 40, "priority": 4, "cut": False}
            for n in names
        ]
        return {"tokens": 10 * len(names), "sections": sections}

    weight = prompt_weight.weigh(
        [measure("task", "wiki"), measure("task", "memory"), measure("task", "wiki")]
    )
    assert weight["wiki_prompts"] == 2 and weight["memory_prompts"] == 1
    merged = prompt_weight.merge(
        [weight, {k: v for k, v in weight.items() if k != "wiki_prompts"}]
    )
    assert merged["wiki_prompts"] == 2 and merged["memory_prompts"] == 2
    off = prompt_weight.weigh([measure("task", "memory")])
    table = "\n".join(
        prompt_weight.render_table(
            {"totals": {"prompt": off}}, {"totals": {"prompt": weight}}
        )
    )
    assert "with the wiki 0 → 2; with vector memory 1 → 1" in table
    assert schema._prompt_problems("t", weight | {"wiki_prompts": -1}) == [
        "t.prompt.wiki_prompts must be a count"
    ]
