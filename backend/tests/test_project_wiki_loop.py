"""The project wiki in the loop (issue #445): compilation at sprint close,
the prompt section and its place before the vector memory, the API, the
bench's counts.

``services/learning/wiki_compile.py``, ``services/loop/wiki_close.py``,
``services/loop/worker/wiki_section.py``, ``routers/wiki.py`` and
``scripts/bench/prompt_weight.py``.
"""

from __future__ import annotations

import asyncio
import secrets
import sys
from pathlib import Path
from uuid import uuid4

import auth_tokens
import pytest
from config import settings
from models import (
    AgentRun,
    Architecture,
    AuditLog,
    BacklogItem,
    Company,
    CompanyMember,
    Specification,
    Sprint,
    User,
    Verification,
    WikiPage,
)
from models.agent_run import TASK_WORKER_PRODUCER
from services.contracts import wiki
from services.learning import wiki_compile, wiki_store
from services.loop import wiki_close
from services.loop.worker import prompt_budget, wiki_section
from sqlmodel import select

SHA = "ef" * 32


def _project(session):
    company = Company(company_name=f"WikiLoop {uuid4().hex[:6]}")
    session.add(company)
    session.commit()
    spec = Specification(
        company_id=company.id,
        title="Spec",
        status="approved",
        requirements=[
            {"id": "REQ-1", "title": "Store rows", "criteria": [{"id": "REQ-1.1"}]}
        ],
    )
    session.add(spec)
    session.commit()
    session.add(
        Architecture(
            company_id=company.id,
            specification_id=spec.id,
            title="Arch",
            status="approved",
            modules=[
                {
                    "id": "MOD-1",
                    "name": "Store",
                    "path": "src/store",
                    "covers": ["REQ-1"],
                    "responsibility": "Keeps rows",
                },
                {"id": "MOD-2", "name": "Api", "path": "src/api", "covers": []},
            ],
            decisions=[
                {"id": "ADR-1", "title": "UTC", "decision": "Store timestamps in UTC."}
            ],
        )
    )
    sprint = Sprint(
        company_id=company.id,
        sprint_name="S1",
        specification_id=spec.id,
        status="completed",
    )
    session.add(sprint)
    session.commit()
    item = BacklogItem(
        sprint_id=sprint.id,
        title="Save rows",
        description="Implement src/store/db.py",
        assignee="Developer",
        covers=["REQ-1.1"],
    )
    session.add(item)
    session.commit()
    run = AgentRun(
        company_id=company.id,
        external_ref=f"task-{item.id}",
        version=1,
        producer=TASK_WORKER_PRODUCER,
        subject={"backlog_item_id": item.id, "title": item.title},
        artifact_digest="0" * 64,
        artifact_size=0,
    )
    session.add(run)
    session.commit()
    session.add(
        Verification(
            company_id=company.id,
            agent_run_id=run.id,
            verdict="verified",
            evidence={
                "workspace_writes": [
                    {
                        "path": "src/store/db.py",
                        "status": "written",
                        "sha256": SHA,
                        "diff": "+def save(): ...",
                    }
                ]
            },
        )
    )
    session.commit()
    return company, sprint, item, run


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


# ── The API ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def api(sqlite_session, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "AUTH_REQUIRED", True)
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "audit"))
    key = secrets.token_urlsafe(48)
    monkeypatch.setattr(auth_tokens, "_configured_key", lambda: key)
    company, _sprint, _item, run = _project(sqlite_session)
    other = Company(company_name=f"Other {uuid4().hex[:6]}")
    sqlite_session.add(other)
    sqlite_session.commit()

    def member(company_id, role):
        user = User(
            email=f"{role.lower()}-{secrets.token_hex(5)}@example.com",
            full_name=role,
            password_hash="x",
        )
        sqlite_session.add(user)
        sqlite_session.commit()
        sqlite_session.add(
            CompanyMember(company_id=company_id, user_id=user.id, human_role=role)
        )
        sqlite_session.commit()
        token = auth_tokens.create_access_token(user_id=user.id, email=user.email)
        return {"Authorization": f"Bearer {token}"}

    return {
        "company": company,
        "run": run,
        "viewer": member(company.id, "Viewer"),
        "manager": member(company.id, "Manager"),
        "outsider": member(other.id, "Manager"),
    }


def test_roles_edit_history_and_decisions(test_client, sqlite_session, api):
    cid, run = api["company"].id, api["run"]
    url = f"/v1/companies/{cid}/wiki/auth"
    body = {
        "kind": "concept",
        "title": "Authentication",
        "body": f"Tokens last 15 minutes [run:{run.id}].\n\n<script>alert(1)</script>\n",
    }
    assert test_client.put(url, json=body).status_code == 401
    assert test_client.put(url, json=body, headers=api["viewer"]).status_code == 403
    assert test_client.put(url, json=body, headers=api["outsider"]).status_code == 403
    written = test_client.put(url, json=body, headers=api["manager"])
    assert written.status_code == 200, written.text
    page = written.json()
    assert page["status"] == "current" and page["person_locked"] is True
    assert page["blocks"][0]["sources"][0] == {
        "kind": "run",
        "ref": str(run.id),
        "known": True,
        "verdict": "verified",
        "task_id": run.subject["backlog_item_id"],
    }
    # The body is stored as written; the UI renders it without raw HTML.
    assert "<script>" in page["body"]
    assert page["links"] == [{"kind": "run", "target": str(run.id), "sha256": None}]

    bad = test_client.put(
        url, json=body | {"body": "Invented [run:99999999]."}, headers=api["manager"]
    )
    assert bad.status_code == 422 and bad.json()["detail"] == {
        "unknown": ["[run:99999999]"]
    }
    assert (
        test_client.put(
            url, json=body | {"kind": "rumour"}, headers=api["manager"]
        ).status_code
        == 422
    )

    listed = test_client.get(f"/v1/companies/{cid}/wiki", headers=api["viewer"])
    assert listed.status_code == 200 and listed.json()["pages"][0]["slug"] == "auth"
    assert "body" not in listed.json()["pages"][0]
    assert (
        test_client.get(
            f"/v1/companies/{cid}/wiki", headers=api["outsider"]
        ).status_code
        == 403
    )

    _page, proposal = wiki_store.save(
        sqlite_session,
        cid,
        slug="auth",
        kind="concept",
        title="Authentication",
        body=f"Tokens last 30 minutes [run:{run.id}].\n",
        author_kind=wiki.AGENT,
        author="TechWriter",
    )
    sqlite_session.commit()
    shown = test_client.get(url, headers=api["viewer"]).json()
    assert (
        shown["proposed_version"]["id"] == proposal.id and "15 minutes" in shown["body"]
    )
    accept = f"/v1/wiki_versions/{proposal.id}/accept"
    assert test_client.post(accept, headers=api["viewer"]).status_code == 403
    accepted = test_client.post(accept, headers=api["manager"])
    assert accepted.status_code == 200 and "30 minutes" in accepted.json()["body"]
    assert test_client.post(accept, headers=api["manager"]).status_code == 409
    history = test_client.get(f"{url}/versions", headers=api["viewer"]).json()[
        "versions"
    ]
    assert [(v["number"], v["author_kind"], v["status"]) for v in history] == [
        (2, "agent", "current"),
        (1, "person", "superseded"),
    ]
    assert (
        test_client.get(
            f"/v1/companies/{cid}/wiki/nope", headers=api["viewer"]
        ).status_code
        == 404
    )

    settings_url = f"/v1/companies/{cid}/wiki-settings"
    assert (
        test_client.put(
            settings_url, json={"write_to_repo": True}, headers=api["viewer"]
        ).status_code
        == 403
    )
    assert test_client.put(
        settings_url, json={"write_to_repo": True}, headers=api["manager"]
    ).json() == {"write_to_repo": True}
    linted = test_client.post(f"/v1/companies/{cid}/wiki-lint", headers=api["manager"])
    assert linted.status_code == 200 and linted.json() == {"stale": {}}
    sqlite_session.expire_all()
    actions = [
        r.action
        for r in sqlite_session.exec(
            select(AuditLog).where(AuditLog.interaction_id == f"company:{cid}:wiki")
        ).all()
    ]
    assert actions == [
        "wiki_page_edited",
        "wiki_version_accepted",
        "wiki_settings_changed",
        "wiki_linted",
    ]


def test_reject_through_the_api(test_client, sqlite_session, api):
    cid, run = api["company"].id, api["run"]
    wiki_store.save(
        sqlite_session,
        cid,
        slug="glossary",
        kind="glossary",
        title="Glossary",
        body="Mine.\n",
        author_kind=wiki.PERSON,
        author="lead@example.com",
    )
    _p, proposal = wiki_store.save(
        sqlite_session,
        cid,
        slug="glossary",
        kind="glossary",
        title="Glossary",
        body=f"Theirs [run:{run.id}].\n",
        author_kind=wiki.AGENT,
        author="TechWriter",
    )
    sqlite_session.commit()
    rejected = test_client.post(
        f"/v1/wiki_versions/{proposal.id}/reject",
        json={"reason": "no"},
        headers=api["manager"],
    )
    assert rejected.status_code == 200 and rejected.json()["proposed_version"] is None
    assert (
        test_client.post(
            "/v1/wiki_versions/999999/reject", json={}, headers=api["manager"]
        ).status_code
        == 404
    )


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
