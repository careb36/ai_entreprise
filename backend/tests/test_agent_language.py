"""Agents write to the person in the company's language (Company.doc_language).

The prompts ask for the person's prose in that language and keep in English
what a program reads (``services/contracts/language.py``); the fixed Glassroom
messages carry a key the live view translates, and every key the backend
sends has its text in both ``en.json`` and ``es.json``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from uuid import uuid4

from models import BacklogItem, Company, Sprint
from services.contracts import language
from services.loop.worker import review_prompt

ROOT = Path(__file__).resolve().parents[2]
WORKER = ROOT / "backend/app/services/loop"
KEY = re.compile(r'"glassroom\.events\.([a-z_]+)"|"(gate_rework|gate_blocked)"')


def _setup(session, doc_language):
    company = Company(company_name=f"Lang {uuid4().hex[:6]}", doc_language=doc_language)
    session.add(company)
    session.commit()
    sprint = Sprint(company_id=company.id, sprint_name="S", status="active")
    session.add(sprint)
    session.commit()
    item = BacklogItem(sprint_id=sprint.id, title="Add the card", status="in_review")
    session.add(item)
    session.commit()
    return sprint, item


def test_the_note_asks_for_spanish_prose_and_keeps_the_markers():
    note = language.person_note("es")
    assert "Spanish" in note
    for kept in ("APPROVED:", "NEEDS WORK:", "code", "commit messages"):
        assert kept in note
    # English is the prompts' own language; unknown values fall back to it.
    assert language.person_note("en") == language.person_note(None) == ""
    assert language.person_note("xx") == ""
    assert language.code(" ES ") == "es"


def test_qa_is_asked_in_the_company_language(sqlite_session):
    reviewer = {"name": "QA", "role": "QA"}
    sprint, item = _setup(sqlite_session, "es")
    prompt = review_prompt.build(sqlite_session, item, sprint, reviewer, "Done.", 0)
    assert prompt.endswith(language.person_note("es"))
    assert "start with 'APPROVED:'" in prompt

    sprint, item = _setup(sqlite_session, "en")
    prompt = review_prompt.build(sqlite_session, item, sprint, reviewer, "Done.", 0)
    assert "LANGUAGE:" not in prompt


def test_every_glassroom_key_the_backend_sends_has_both_texts():
    sent = set()
    for path in WORKER.rglob("*.py"):
        for named, gate in KEY.findall(path.read_text()):
            sent.add(named or gate)
    assert {"recovered", "approved", "gate_rework", "awaiting_person"} <= sent
    for locale in ("en", "es"):
        events = json.loads((ROOT / f"frontend/src/i18n/{locale}.json").read_text())[
            "glassroom"
        ]["events"]
        assert sent <= set(events), (locale, sent - set(events))
