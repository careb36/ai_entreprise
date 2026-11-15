"""The project wiki (issue #445): its syntax, its store, its lint.

The syntax (``services/contracts/wiki.py``: citations, statement blocks,
facts, links), the citations resolved against the records
(``services/learning/wiki_sources.py``), the store and the person's
precedence (``services/learning/wiki_store.py``) and the three lint rules
(``services/contracts/wiki_lint.py``, run by ``services/loop/wiki_close.lint_company``).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from models import (
    AgentRun,
    Architecture,
    BacklogItem,
    Company,
    Specification,
    Sprint,
    SprintBoardNote,
    Verification,
    WikiLink,
    WikiPage,
)
from models.agent_run import TASK_WORKER_PRODUCER
from services.contracts import wiki, wiki_lint, wiki_templates
from services.learning import wiki_sources, wiki_store
from services.loop import wiki_close
from sqlmodel import select

SHA = "ab" * 32
SHA2 = "cd" * 32

# ── The syntax ───────────────────────────────────────────────────────────────


def test_citations_are_read_in_every_form():
    found = wiki.citations(
        "A [run:12] b [verification:3] [task:7] [note:9] [retro:4] "
        f"[file:./src/a.py@{SHA[:12]}] [REQ-1.2] [NFR-1] [ADR-3] [MOD-2]"
    )
    assert [(c["kind"], c["ref"]) for c in found] == [
        ("run", "12"),
        ("verification", "3"),
        ("task", "7"),
        ("note", "9"),
        ("retro", "4"),
        ("file", "src/a.py"),
        ("req", "REQ-1.2"),
        ("req", "NFR-1"),
        ("adr", "ADR-3"),
        ("mod", "MOD-2"),
    ]
    assert found[5]["sha256"] == SHA[:12]


@pytest.mark.parametrize(
    "bad",
    [
        "[run:abc]",
        f"[file:../etc/passwd@{SHA}]",
        "[file:src/a.py]",
        "[file:/abs.py@abcdefabcdef]",
    ],
)
def test_malformed_citations_are_named(bad):
    assert wiki.citations(bad) == [] or wiki.bad_citations(bad)
    assert wiki.bad_citations(bad) == [bad]


def test_statement_blocks_and_uncited_ones():
    body = (
        "# Title\n\nCited paragraph [run:1].\nstill it.\n\nUncited paragraph.\n\n"
        "- item one [ADR-1]\n- item two names only [MOD-1]\n\n"
        "| a | b |\n|---|---|\n| x [task:2] | y |\n| z | w |\n\n"
        "```python\nno citation needed in code\n```\n\n"
        "```facts\nauth.ttl: 15 minutes [run:1]\ndb.engine: postgres\n```\n"
    )
    blocks = wiki.statement_blocks(body)
    kinds = [b["kind"] for b in blocks]
    assert kinds == [
        "heading",
        "paragraph",
        "paragraph",
        "item",
        "item",
        "table_header",
        "row",
        "row",
        "code",
        "fact",
        "fact",
    ]
    assert [u["excerpt"] for u in wiki.uncited(blocks)] == [
        "Uncited paragraph.",
        "- item two names only [MOD-1]",
        "| z | w |",
        "db.engine: postgres",
    ]


def test_facts_are_read_without_their_citations():
    body = "```facts\nauth.token_ttl: 15  Minutes [run:3] [ADR-1]\nauth.token_ttl: other\nNot a fact\n```"
    assert [(f["key"], f["value"]) for f in wiki.facts(body)] == [
        ("auth.token_ttl", "15 Minutes")
    ]
    assert wiki.normalize_fact("15 Minutes.") == wiki.normalize_fact("15 minutes")


def test_links_are_derived_from_the_body_citations_and_bare_ids():
    links = wiki.derive_links(
        f"Uses [run:5] and [file:src/a.py@{SHA}]. Covers REQ-2 and MOD-1; [ADR-1] too, ADR-1."
    )
    assert [(lk["kind"], lk["target"]) for lk in links] == [
        ("run", "5"),
        ("file", "src/a.py"),
        ("adr", "ADR-1"),
        ("req", "REQ-2"),
        ("mod", "MOD-1"),
    ]
    assert links[1]["sha256"] == SHA


def test_control_lines_are_quoted_for_the_prompt():
    text = wiki.for_prompt("ok\n```python path=src/x.py\nNOTE kind=decision\nEND\nfine")
    assert text.split("\n") == [
        "ok",
        "> ```python path=src/x.py",
        "> NOTE kind=decision",
        "> END",
        "fine",
    ]


def test_spanish_scaffolding_keeps_the_syntax():
    assert wiki_templates.sections("module", "es")[0] == "Responsabilidad"
    assert wiki_templates.language_of(type("C", (), {"doc_language": "es"})()) == "es"
    assert wiki_templates.language_of(type("C", (), {})()) == "en"
    assert wiki_templates.language_of(type("C", (), {"doc_language": "fr"})()) == "en"
    reason = {"rule": "file_missing", "path": "src/a.py"}
    assert (
        wiki_templates.describe_reason(reason, "es")
        == "cita src/a.py, que ya no está en el disco"
    )


# ── Records ──────────────────────────────────────────────────────────────────


def _company(session, **fields):
    company = Company(company_name=f"Wiki {uuid4().hex[:6]}", **fields)
    session.add(company)
    session.commit()
    sprint = Sprint(company_id=company.id, sprint_name="S1", status="completed")
    session.add(sprint)
    session.commit()
    item = BacklogItem(sprint_id=sprint.id, title="Store", assignee="Developer")
    session.add(item)
    session.commit()
    return company, sprint, item


def _run(session, company, item, writes, verdict="verified", at=None):
    run = AgentRun(
        company_id=company.id,
        external_ref=f"task-{item.id}",
        version=len(
            session.exec(
                select(AgentRun).where(AgentRun.external_ref == f"task-{item.id}")
            ).all()
        )
        + 1,
        producer=TASK_WORKER_PRODUCER,
        subject={"backlog_item_id": item.id, "title": item.title},
        artifact_digest="0" * 64,
        artifact_size=0,
    )
    session.add(run)
    session.commit()
    verification = Verification(
        company_id=company.id,
        agent_run_id=run.id,
        verdict=verdict,
        evidence={
            "workspace_writes": [
                {"path": p, "status": "written", "sha256": s} for p, s in writes
            ]
        },
    )
    if at is not None:
        verification.observed_at = at
    session.add(verification)
    session.commit()
    return run, verification


def test_citations_resolve_only_against_the_companys_records(sqlite_session):
    company, sprint, item = _company(sqlite_session)
    other, _s, other_item = _company(sqlite_session)
    run, verification = _run(sqlite_session, company, item, [("src/a.py", SHA)])
    foreign, _ = _run(sqlite_session, other, other_item, [("src/a.py", SHA2)])
    note = SprintBoardNote(
        company_id=company.id,
        sprint_id=sprint.id,
        kind="decision",
        text="x",
        author_role="Developer",
    )
    sqlite_session.add(note)
    sqlite_session.add(
        Specification(
            company_id=company.id,
            title="S",
            status="approved",
            requirements=[
                {"id": "REQ-1", "title": "Login", "criteria": [{"id": "REQ-1.1"}]}
            ],
        )
    )
    sqlite_session.commit()
    body = (
        f"[run:{run.id}] [verification:{verification.id}] [task:{item.id}] [note:{note.id}] "
        f"[retro:{sprint.id}] [file:src/a.py@{SHA[:16]}] [REQ-1.1] "
        f"[run:{foreign.id}] [file:src/a.py@{SHA2[:16]}] [task:{other_item.id}] [ADR-9]"
    )
    resolved = wiki_sources.resolve(sqlite_session, company.id, wiki.citations(body))
    assert [s["known"] for s in resolved] == [True] * 7 + [False] * 4
    assert resolved[0]["verdict"] == "verified"
    assert resolved[5]["run_id"] == run.id
    assert wiki_sources.unknown(resolved) == [
        f"[run:{foreign.id}]",
        f"[file:src/a.py@{SHA2[:16]}]",
        f"[task:{other_item.id}]",
        "[ADR-9]",
    ]


# ── The store ────────────────────────────────────────────────────────────────


def _save(
    session, company, body, author_kind=wiki.AGENT, slug="mod-store", kind="module"
):
    page, version = wiki_store.save(
        session,
        company.id,
        slug=slug,
        kind=kind,
        title="Store",
        body=body,
        author_kind=author_kind,
        author="TechWriter" if author_kind == wiki.AGENT else "lead@example.com",
        module={"id": "MOD-1", "name": "Store", "path": "src/store"},
    )
    session.commit()
    return page, version


def test_an_unknown_citation_is_refused(sqlite_session):
    company, _sprint, _item = _company(sqlite_session)
    with pytest.raises(wiki_store.WikiError) as exc:
        _save(sqlite_session, company, "The store [run:999999].")
    assert exc.value.detail == {"unknown": ["[run:999999]"]}
    with pytest.raises(wiki_store.WikiError):
        _save(sqlite_session, company, "Bad [run:x].")


def test_a_cited_agent_page_is_in_force_and_its_links_are_derived(sqlite_session):
    company, _sprint, item = _company(sqlite_session)
    run, _ = _run(sqlite_session, company, item, [("src/store/db.py", SHA)])
    page, version = _save(
        sqlite_session,
        company,
        f"# Store\n\nSaves rows [run:{run.id}] [file:src/store/db.py@{SHA[:12]}].\n",
    )
    assert version.status == wiki.CURRENT and page.status == wiki.CURRENT
    assert page.version == 1 and not page.person_locked
    links = sqlite_session.exec(
        select(WikiLink).where(WikiLink.page_id == page.id)
    ).all()
    assert {(lk.kind, lk.target) for lk in links} == {
        ("run", str(run.id)),
        ("file", "src/store/db.py"),
    }
    assert version.sources[0]["known"] is True


def test_an_uncited_agent_statement_makes_a_proposal(sqlite_session):
    company, _sprint, item = _company(sqlite_session)
    run, _ = _run(sqlite_session, company, item, [])
    page, version = _save(
        sqlite_session, company, f"Cited [run:{run.id}].\n\nNot cited.\n"
    )
    assert version.status == wiki.PROPOSED and page.status == wiki.PROPOSED
    assert page.body == "" and page.proposed_version_id == version.id
    assert version.findings == [{"line": 3, "excerpt": "Not cited."}]


def test_a_persons_edit_wins_and_the_next_compilation_only_proposes(sqlite_session):
    company, _sprint, item = _company(sqlite_session)
    run, _ = _run(sqlite_session, company, item, [])
    _save(sqlite_session, company, f"Agent text [run:{run.id}].\n")
    page, mine = _save(
        sqlite_session, company, "A person's text, no citation needed.\n", wiki.PERSON
    )
    assert mine.status == wiki.CURRENT and page.person_locked
    page, proposal = _save(sqlite_session, company, f"Agent again [run:{run.id}].\n")
    assert proposal.status == wiki.PROPOSED
    assert page.body == "A person's text, no citation needed.\n"
    _page, second = _save(sqlite_session, company, f"Agent third [run:{run.id}].\n")
    sqlite_session.refresh(proposal)
    assert proposal.status == wiki.SUPERSEDED and page.proposed_version_id == second.id

    page = wiki_store.accept(sqlite_session, second, "lead@example.com")
    sqlite_session.commit()
    assert (
        page.body == f"Agent third [run:{run.id}].\n" and page.version == second.number
    )
    assert second.decided_by == "lead@example.com" and not page.person_locked
    with pytest.raises(wiki_store.WikiError):
        wiki_store.accept(sqlite_session, second, "lead@example.com")
    _page, rejected = _save(
        sqlite_session, company, f"Agent fourth [run:{run.id}].\n", slug="mod-store"
    )
    # Not locked any more: the agent's version went in force.
    assert rejected.status == wiki.CURRENT


def test_a_rejected_proposal_leaves_the_page(sqlite_session):
    company, _sprint, item = _company(sqlite_session)
    run, _ = _run(sqlite_session, company, item, [])
    _save(sqlite_session, company, "Mine.\n", wiki.PERSON)
    page, proposal = _save(sqlite_session, company, f"Theirs [run:{run.id}].\n")
    wiki_store.reject(sqlite_session, proposal, "lead@example.com", "not now")
    sqlite_session.commit()
    assert proposal.status == wiki.REJECTED and proposal.decision_reason == "not now"
    assert page.body == "Mine.\n" and page.proposed_version_id is None


# ── The lint ─────────────────────────────────────────────────────────────────


def test_rule_one_missing_or_changed_files():
    body = f"[file:src/a.py@{SHA[:12]}] [file:src/b.py@{SHA[:12]}] [file:src/c.py@{SHA[:12]}] [file:src/d.py@{SHA[:12]}]"
    reasons = wiki_lint.file_findings(
        body,
        {
            "src/a.py": {"status": "missing"},
            "src/b.py": {"status": "present", "sha256": SHA2},
            "src/c.py": {"status": "present", "sha256": SHA},
        },
    )
    assert [(r["rule"], r["path"]) for r in reasons] == [
        ("file_missing", "src/a.py"),
        ("file_changed", "src/b.py"),
    ]


def test_rule_two_module_changed_after_the_version():
    written = datetime(2026, 11, 1)
    page = {"body": f"[file:lib/x.py@{SHA[:12]}]", "module_path": "src/store"}
    writes = [
        {
            "run_id": 1,
            "path": "src/store/db.py",
            "observed_at": written - timedelta(days=1),
        },
        {
            "run_id": 2,
            "path": "src/store/db.py",
            "observed_at": written + timedelta(days=1),
        },
        {"run_id": 3, "path": "lib/x.py", "observed_at": written + timedelta(days=1)},
        {
            "run_id": 4,
            "path": "src/storage.py",
            "observed_at": written + timedelta(days=1),
        },
    ]
    reasons = wiki_lint.module_findings(page, written, writes)
    assert [r["run_id"] for r in reasons] == [2, 3]


def test_rule_three_contradiction_on_a_named_fact():
    found = wiki_lint.contradictions(
        [
            {
                "slug": "auth",
                "body": "```facts\nauth.ttl: 15 minutes [run:1]\ndb: pg [run:1]\n```",
            },
            {
                "slug": "deploy",
                "body": "```facts\nauth.ttl: 30 minutes [run:2]\ndb: PG. [run:2]\n```",
            },
            {"slug": "other", "body": "```facts\nauth.ttl: 15 Minutes [run:3]\n```"},
        ]
    )
    assert sorted(found) == ["auth", "deploy", "other"]
    assert [(r["key"], r["other_page"]) for r in found["deploy"]] == [
        ("auth.ttl", "auth"),
        ("auth.ttl", "other"),
    ]
    assert [r["other_page"] for r in found["auth"]] == ["deploy"]


def test_lint_marks_pages_stale_and_only_a_revision_clears_it(sqlite_session):
    company, _sprint, item = _company(sqlite_session)
    run, _ = _run(
        sqlite_session,
        company,
        item,
        [("src/store/db.py", SHA)],
        at=datetime(2026, 1, 1),
    )
    page, _v = _save(
        sqlite_session,
        company,
        f"Rows [run:{run.id}] [file:src/store/db.py@{SHA[:12]}].\n",
    )
    assert wiki_close.lint_company(sqlite_session, company.id) == {}
    # A later verified run changes the module and the file.
    _run(
        sqlite_session,
        company,
        item,
        [("src/store/db.py", SHA2)],
        at=datetime(2100, 1, 1),
    )
    found = wiki_close.lint_company(sqlite_session, company.id)
    sqlite_session.commit()
    assert {r["rule"] for r in found["mod-store"]} == {"file_changed", "module_changed"}
    sqlite_session.refresh(page)
    assert page.status == wiki.STALE
    # The disk's word wins over the evidence: a missing file.
    found = wiki_close.lint_company(
        sqlite_session, company.id, {"src/store/db.py": {"status": "missing"}}
    )
    assert found["mod-store"][0]["rule"] == "file_missing"
    page, _v = _save(sqlite_session, company, "Revised by a person.\n", wiki.PERSON)
    assert page.status == wiki.CURRENT and page.lint_reasons == []


def test_stale_and_proposed_pages_are_not_in_force(sqlite_session):
    company, _sprint, item = _company(sqlite_session)
    run, _ = _run(sqlite_session, company, item, [])
    _save(sqlite_session, company, f"ok [run:{run.id}].\n", slug="mod-a")
    _save(sqlite_session, company, "no source.\n", slug="mod-b")
    stale, _ = _save(sqlite_session, company, f"ok [run:{run.id}].\n", slug="mod-c")
    wiki_store.apply_lint(
        sqlite_session, stale, [{"rule": "file_missing", "path": "x"}]
    )
    sqlite_session.commit()
    assert [p.slug for p in wiki_store.in_force(sqlite_session, company.id)] == [
        "mod-a"
    ]
    assert sqlite_session.exec(
        select(WikiPage).where(WikiPage.company_id == company.id)
    ).all()


def test_the_architecture_names_adrs_and_modules(sqlite_session):
    company, _sprint, _item = _company(sqlite_session)
    spec = Specification(company_id=company.id, title="S", status="approved")
    sqlite_session.add(spec)
    sqlite_session.commit()
    sqlite_session.add(
        Architecture(
            company_id=company.id,
            specification_id=spec.id,
            title="A",
            status="approved",
            modules=[{"id": "MOD-1", "name": "Store"}],
            decisions=[{"id": "ADR-1", "title": "UTC"}],
        )
    )
    sqlite_session.commit()
    resolved = wiki_sources.resolve(
        sqlite_session, company.id, wiki.citations("[ADR-1] [MOD-1] [ADR-2]")
    )
    assert [s["known"] for s in resolved] == [True, True, False]
