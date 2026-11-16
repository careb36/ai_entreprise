"""The project wiki compiled at sprint close (issue #445): the touched pages
written with their sources, in English or Spanish, an unknown citation or a
failed call recorded, a person-edited page only proposed, the page and answer
bounds, ``after_close`` never raising, and the pages written to the repository
through the daemon (``services/learning/wiki_compile.py``,
``services/loop/wiki_close.py``). The prompt section is in
``test_project_wiki_prompt.py``, the API in ``test_project_wiki_api.py``;
the shared rows in ``project_wiki_helpers``.
"""

from __future__ import annotations

import asyncio

from config import settings
from models import (
    AuditLog,
)
from services.contracts import wiki
from services.learning import wiki_compile, wiki_store
from services.loop import wiki_close
from sqlmodel import select

from project_wiki_helpers import SHA, _project


def _answer(run_id: int, heading: str = "Responsibility"):
    return (
        "```markdown\n# MOD-1 · Store\n\n"
        f"## {heading}\n\nKeeps rows in SQLite [run:{run_id}] [file:src/store/db.py@{SHA[:12]}].\n\n"
        f"```facts\nstore.engine: sqlite [run:{run_id}]\n```\n```"
    )


def test_compiling_a_sprint_writes_the_touched_pages_with_sources(
    sqlite_session, monkeypatch
):
    company, sprint, _item, run = _project(sqlite_session)
    inputs = wiki_close.gather_inputs(sqlite_session, sprint.id, {"total_tasks": 1})
    assert inputs["language"] == "en"
    assert inputs["runs"][0]["files"][0] == {
        "path": "src/store/db.py",
        "sha256": SHA,
        "diff": "+def save(): ...",
    }
    requests = []

    def ask(role, model, system, prompt, company_id):
        requests.append((role, prompt))
        if "glossary" in prompt.split("\n")[0]:
            return "# Glossary\n\n- **Row**: a record [REQ-1]\n"
        return _answer(run.id)

    monkeypatch.setattr(settings, "WIKI_COMPILE_MAX_PAGES", 5)
    summary = wiki_compile.compile_sprint(sqlite_session, inputs, ask=ask)
    assert summary["role"] == "ScrumMaster"  # no TechWriter in this company
    assert [e["slug"] for e in summary["compiled"]] == [
        "decisions",
        "mod-store",
        "glossary",
    ]
    assert summary["decisions"] == "compiled" and not summary["failed"]
    # Only the module a verified run touched; the request carries the records.
    first = requests[0][1]
    assert "src/store/db.py sha256 " + SHA in first and "+def save()" in first
    assert "Write the page in English" in first and "'## Responsibility'" in first
    page = wiki_store.page_of(sqlite_session, company.id, "mod-store")
    assert page.status == wiki.CURRENT and page.module_path == "src/store"
    assert not page.body.startswith("```")
    decisions = wiki_store.page_of(sqlite_session, company.id, "decisions")
    assert "[ADR-1]" in decisions.body and decisions.status == wiki.CURRENT


def test_compiling_in_spanish_gives_spanish_scaffolding_and_the_same_citations(
    sqlite_session, monkeypatch
):
    company, sprint, _item, run = _project(sqlite_session)
    # The company's doc_language (#530) is not a column on main yet: read
    # defensively through language_of, here as if the company said "es".
    monkeypatch.setattr(
        wiki_close.wiki_templates,
        "language_of",
        lambda c: "es" if c.id == company.id else "en",
    )
    inputs = wiki_close.gather_inputs(sqlite_session, sprint.id)
    assert inputs["language"] == "es"
    prompts = []

    def ask(role, model, system, prompt, company_id):
        prompts.append(prompt)
        return _answer(run.id, heading="Responsabilidad")

    summary = wiki_compile.compile_sprint(sqlite_session, inputs, ask=ask)
    assert "Escribe la página en español" in prompts[0]
    assert "'## Responsabilidad'" in prompts[0] and "'## Cómo funciona'" in prompts[0]
    # The syntax is the same in every language.
    assert "[run:<id>]" in prompts[0] and "`facts`" in prompts[0]
    assert "# Glosario" in prompts[-1] or "Glosario" in prompts[-1]
    decisions = wiki_store.page_of(sqlite_session, company.id, "decisions")
    assert decisions.title == "Decisiones vigentes"
    assert "## Decisiones vigentes" in decisions.body and "[ADR-1]" in decisions.body
    page = wiki_store.page_of(sqlite_session, company.id, "mod-store")
    assert (
        f"[run:{run.id}]" in page.body
        and f"[file:src/store/db.py@{SHA[:12]}]" in page.body
    )
    assert summary["language"] == "es"


def test_a_compilation_with_an_unknown_citation_or_a_failed_call_is_recorded(
    sqlite_session,
):
    _company, sprint, _item, _run = _project(sqlite_session)
    inputs = wiki_close.gather_inputs(sqlite_session, sprint.id)
    calls = {"n": 0}

    def ask(role, model, system, prompt, company_id):
        calls["n"] += 1
        if calls["n"] == 1:
            return "# Store\n\nInvented [run:99999999].\n"
        raise wiki_compile.WikiCompileError("LLM call failed: down")

    summary = wiki_compile.compile_sprint(sqlite_session, inputs, ask=ask)
    assert summary["rejected"][0]["slug"] == "mod-store"
    assert "[run:99999999]" in summary["rejected"][0]["reason"]
    assert summary["failed"] == [{"slug": "glossary", "error": "LLM call failed: down"}]


def test_compilation_proposes_on_a_page_a_person_edited(sqlite_session):
    company, sprint, _item, run = _project(sqlite_session)
    wiki_store.save(
        sqlite_session,
        company.id,
        slug="mod-store",
        kind="module",
        title="Store",
        body="Written by a person.\n",
        author_kind=wiki.PERSON,
        author="lead@example.com",
    )
    sqlite_session.commit()
    inputs = wiki_close.gather_inputs(sqlite_session, sprint.id)
    summary = wiki_compile.compile_sprint(
        sqlite_session, inputs, ask=lambda *a: _answer(run.id)
    )
    assert {"slug": "mod-store", "version": 2} in summary["proposed"]
    page = wiki_store.page_of(sqlite_session, company.id, "mod-store")
    assert page.body == "Written by a person.\n" and page.proposed_version_id


def test_the_page_bound_and_the_answer_bound(sqlite_session, monkeypatch):
    _company, sprint, _item, _run = _project(sqlite_session)
    inputs = wiki_close.gather_inputs(sqlite_session, sprint.id)
    monkeypatch.setattr(settings, "WIKI_COMPILE_MAX_PAGES", 1)
    assert [t["slug"] for t in wiki_compile.plan(sqlite_session, inputs)] == [
        "mod-store"
    ]
    monkeypatch.setattr(settings, "WIKI_PAGE_MAX_CHARS", 50)
    summary = wiki_compile.compile_sprint(
        sqlite_session, inputs, ask=lambda *a: "# x\n" + "y" * 100
    )
    assert summary["rejected"] == [
        {"slug": "mod-store", "reason": "over 50 characters"}
    ]


def test_after_close_never_raises_and_audits(sqlite_session, monkeypatch):
    engine = sqlite_session.get_bind()
    _company, sprint, _item, _run = _project(sqlite_session)
    monkeypatch.setattr(wiki_close, "engine", engine)
    from services.loop.worker import deliver

    monkeypatch.setattr(deliver, "_resolve_workspace_root", lambda cid: None)
    # The conftest's proxy refuses every call: every page fails, the close goes on.
    summary = asyncio.run(wiki_close.after_close(None, sprint.id, {"total_tasks": 1}))
    assert summary["failed"] and summary["decisions"] == "compiled"
    sqlite_session.expire_all()
    rows = sqlite_session.exec(
        select(AuditLog).where(AuditLog.interaction_id == f"sprint:{sprint.id}:wiki")
    ).all()
    assert [r.action for r in rows] == ["wiki_compiled"]
    monkeypatch.setattr(settings, "WIKI_ENABLED", False)
    assert asyncio.run(wiki_close.after_close(None, sprint.id, None)) == {
        "skipped": "WIKI_ENABLED is off"
    }


def test_pages_are_written_to_the_repo_through_the_daemon(sqlite_session, monkeypatch):
    company, sprint, _item, run = _project(sqlite_session)
    wiki_store.save(
        sqlite_session,
        company.id,
        slug="mod-store",
        kind="module",
        title="Store",
        body=f"Rows [run:{run.id}].\n",
        author_kind=wiki.AGENT,
        author="TechWriter",
    )
    sqlite_session.commit()
    files = wiki_close.repo_files(sqlite_session, company.id, ["mod-store", "missing"])
    assert [f["path"] for f in files] == ["docs/wiki/mod-store.md"]
    assert files[0]["content"].startswith("<!-- Project wiki page 'mod-store' v1")
    sent = {}

    async def fake_request(nats, subject, payload, timeout):
        sent.update(subject=subject, payload=payload)
        return {
            "files": [
                {"path": "docs/wiki/mod-store.md", "status": "written", "sha256": SHA}
            ],
            "git": {"branch": "fabrique/wiki"},
        }

    monkeypatch.setattr(wiki_close.workspace_daemon, "daemon_request", fake_request)
    written = asyncio.run(
        wiki_close.write_pages(
            None,
            {"root": "/w", "git_workflow": True, "git_base_branch": "main"},
            sprint.id,
            files,
        )
    )
    assert sent["subject"] == "workspace.generate"
    assert sent["payload"]["git"]["branch"] == "fabrique/wiki"
    assert written["files"][0]["sha256"] == SHA


def test_disk_hashes_only_without_the_git_workflow(monkeypatch):
    async def fake_request(nats, subject, payload, timeout):
        return {
            "files": [
                {"path": "a.py", "status": "missing"},
                {"path": "b.py", "status": "rejected"},
            ]
        }

    monkeypatch.setattr(wiki_close.workspace_daemon, "daemon_request", fake_request)
    assert (
        asyncio.run(
            wiki_close.disk_hashes(None, {"root": "/w", "git_workflow": True}, ["a.py"])
        )
        is None
    )
    assert asyncio.run(
        wiki_close.disk_hashes(None, {"root": "/w", "git_workflow": False}, ["a.py"])
    ) == {"a.py": {"status": "missing", "sha256": None}}
