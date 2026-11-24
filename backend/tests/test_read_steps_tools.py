"""The steps that only read get every reading tool their step allows (issue #766).

``docs/design/agent-toolsets.md``: family R is list, read, search, find_file
and read-only Git (``blame`` included), and a document's text; the
Architect's brief also researches (family S); the ScrumMaster's decision
reads the sprint's records and the PO's plan the company's board (family E).
The read-only toolboxes of the QA review, the Architect's brief, the PO's
planning and the ScrumMaster's triage offer ``workspace__git`` and
``workspace__read_document`` when every daemon announces them; the brief adds
the research tools, the triage and the planning the record tools, each
beside the reads only, never turning a turn without tools into a tool loop,
and never an edit, a test run or the lint.
"""

from __future__ import annotations

import developer_tools_helpers
import pytest
from config import settings
from developer_tools_helpers import _call, _load_daemon, _Requester
from models.company import Company
from models.sprint import BacklogItem, Sprint
from services.loop.worker import architect_brief, triage_reads
from services.planning import planning_reads
from services.tools import developer_tools, review_tools
from services.workspace import daemon_liveness

project = developer_tools_helpers.project

READS = {"workspace__git", "workspace__read_document"}
NEVER = {"workspace__edit", "workspace__move", "workspace__delete"}
NEVER |= {"workspace__run_tests", "workspace__lint"}
RESEARCH = {"research__package", "research__advisories", "research__fetch"}
BOARD = {"board__task", "board__evidence"}


def _names(box):
    return {s["function"]["name"] for s in (box.schemas() if box else [])}


@pytest.fixture()
def bound(monkeypatch, project, sqlite_session):
    """The worker's daemon bound to *project*; the review's root is it too;
    the test database for the reads that look up a task."""
    monkeypatch.setattr(triage_reads, "engine", sqlite_session.get_bind())
    monkeypatch.setattr(planning_reads, "engine", sqlite_session.get_bind())
    monkeypatch.setattr(
        developer_tools, "_request_from_thread", _Requester(_load_daemon())
    )
    monkeypatch.setattr(
        developer_tools,
        "_bridge",
        {"nats": object(), "resolve_root": lambda company_id: str(project)},
    )
    monkeypatch.setattr(review_tools, "delivery_root", lambda *a, **k: str(project))
    monkeypatch.setattr(settings, "RESEARCH_TOOLS_ENABLED", True)
    monkeypatch.setattr(settings, "BOARD_TOOLS_ENABLED", True)
    for name in ("ARCHITECT", "PLANNING", "SCRUM_TRIAGE", "QA_REVIEW"):
        monkeypatch.setattr(settings, f"{name}_TOOL_ROUNDS", 3)


@pytest.fixture()
def announced():
    """Every daemon heard announces read-only Git with blame and a document's text."""
    daemon_liveness.reset()
    daemon_liveness.observe_heartbeat(
        {
            "daemon_id": "d1",
            "abilities": {
                "abilities_version": 1,
                "subjects": [
                    "workspace.git.diff",
                    "workspace.git.read_at",
                    "workspace.read",
                ],
                "features": {
                    "workspace.git.diff": ["view", "blame"],
                    "workspace.read": ["document_text"],
                },
            },
        }
    )
    yield
    daemon_liveness.reset()


def _read_only_boxes():
    return {
        "read_tools": developer_tools.read_tools(1, 2),
        "qa_review": review_tools.read_only_toolbox(1, 2, engine=None, rounds=3),
        "architect_brief": architect_brief.read_tools(1, 2, None),
        "plan": planning_reads.toolbox(1),
        "scrum_decision": triage_reads.toolbox(1, 2),
    }


def test_every_read_only_toolbox_offers_git_and_documents_when_announced(
    bound, announced
):
    for step, box in _read_only_boxes().items():
        names = _names(box)
        assert READS <= names, step
        assert not names & NEVER, step
        box.close()
    # Blame is among the read-only Git operations offered.
    box = developer_tools.read_tools(1, 2)
    schema = next(s for s in box.schemas() if s["function"]["name"] == "workspace__git")
    assert "blame" in str(schema)


def test_not_announced_not_offered(bound):
    daemon_liveness.reset()
    for step, box in _read_only_boxes().items():
        assert not _names(box) & READS, step


def test_the_architect_brief_researches_beside_its_reads(bound):
    box = architect_brief.read_tools(1, 2, None)
    names = _names(box)
    assert RESEARCH <= names and "workspace__read" in names
    assert not names & NEVER
    # Within the brief's rounds, and the request says what the tools may do.
    assert box.max_rounds == settings.ARCHITECT_TOOL_ROUNDS
    assert "research tools" in architect_brief.note_for(box)
    # Research never turns the plain call into a tool loop.
    developer_tools.unbind()
    assert architect_brief.read_tools(1, 2, None) is None
    # The QA review's step does not research.
    assert not _names(review_tools.read_only_toolbox(1, 2, engine=None, rounds=3)) & (
        RESEARCH
    )


@pytest.fixture()
def board(sqlite_session, monkeypatch):
    """Two sprints of one company and a task of another company."""
    ours, theirs = Company(company_name="Ours"), Company(company_name="Theirs")
    sqlite_session.add_all([ours, theirs])
    sqlite_session.commit()
    s1 = Sprint(company_id=ours.id, sprint_name="S1", scope_size="S")
    s2 = Sprint(company_id=ours.id, sprint_name="S2", scope_size="S")
    s3 = Sprint(company_id=theirs.id, sprint_name="X", scope_size="S")
    sqlite_session.add_all([s1, s2, s3])
    sqlite_session.commit()
    rows = {
        "blocked": BacklogItem(sprint_id=s1.id, title="Blocked", status="blocked"),
        "sibling": BacklogItem(sprint_id=s1.id, title="Sibling", status="done"),
        "earlier": BacklogItem(sprint_id=s2.id, title="Earlier", status="done"),
        "theirs": BacklogItem(sprint_id=s3.id, title="Theirs", status="done"),
    }
    sqlite_session.add_all(rows.values())
    sqlite_session.commit()
    return ours.id, {k: v.id for k, v in rows.items()}


def _read(box, task_id):
    obs, text = box.call(_call("board__task", task_id=task_id), 1)
    return obs, text


def test_the_scrum_master_reads_the_sprints_records(bound, board):
    company_id, ids = board
    box = triage_reads.toolbox(company_id, ids["blocked"])
    assert BOARD <= _names(box) and "workspace__read" in _names(box)
    assert box.max_rounds == settings.SCRUM_TRIAGE_TOOL_ROUNDS
    obs, text = _read(box, ids["sibling"])
    assert not obs["is_error"] and "Sibling" in text
    for outside in ("earlier", "theirs"):  # Another sprint, another company.
        obs, _ = _read(box, ids[outside])
        assert obs["refused"] == "outside the turn's scope", outside


def test_the_po_reads_the_companys_board(bound, board):
    company_id, ids = board
    box = planning_reads.toolbox(company_id)
    assert BOARD <= _names(box) and "workspace__read" in _names(box)
    assert box.max_rounds == settings.PLANNING_TOOL_ROUNDS
    for ours in ("blocked", "earlier"):
        obs, _ = _read(box, ids[ours])
        assert not obs["is_error"], ours
    obs, _ = _read(box, ids["theirs"])
    assert obs["refused"] == "outside the turn's scope"


def test_the_record_tools_never_widen_a_turn_without_tools(bound, board, monkeypatch):
    company_id, ids = board
    monkeypatch.setattr(settings, "BOARD_TOOLS_ENABLED", False)
    assert not _names(planning_reads.toolbox(company_id)) & BOARD
    assert not _names(triage_reads.toolbox(company_id, ids["blocked"])) & BOARD
    monkeypatch.setattr(settings, "BOARD_TOOLS_ENABLED", True)
    developer_tools.unbind()  # No daemon: no reads, so no record tools either.
    assert triage_reads.toolbox(company_id, ids["blocked"]) is None
    assert planning_reads.toolbox(company_id) is None
