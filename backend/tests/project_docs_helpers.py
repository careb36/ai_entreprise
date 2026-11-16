"""What the tests of the documentation pack's builders share (issue #530): a
snapshot of a project's records, an empty one, the pack built from a
snapshot and a pack read back for the drift lint. The tests are
``test_project_docs*.py``; ``test_project_docs_language.py`` and
``test_wstg_plan.py`` import ``_snapshot``, ``_empty`` and ``_Pack`` from
``test_project_docs``, which re-exports them.
"""

from services.planning.project_docs import (
    pack,
)


def _snapshot():
    spec = {
        "id": 3,
        "version": 2,
        "title": "Board service",
        "summary": "A board for a small team.",
        "status": "approved",
        "approved_at": "2026-11-01T10:00:00",
        "drafted_by_role": "PO",
        "requirements": [
            {
                "id": "REQ-1",
                "title": "List tasks",
                "description": "Show every task.",
                "criteria": [
                    {
                        "id": "REQ-1.1",
                        "given": "tasks",
                        "when": "I open the board",
                        "then": "I see them",
                    }
                ],
            },
            {"id": "REQ-2", "title": "Move tasks", "description": "", "criteria": []},
        ],
        "quality": [{"id": "NFR-1", "attribute": "Latency", "target": "under 200 ms"}],
        "constraints": ["Python 3.11"],
        "questions": [{"question": "Which browsers?", "answer": None}],
        "sha256": "ab" * 32,
    }
    arch = {
        "id": 5,
        "version": 1,
        "title": "Board architecture",
        "overview": "An API and a web client.",
        "status": "approved",
        "approved_at": "2026-11-02T10:00:00",
        "drafted_by_role": "Architect",
        "modules": [
            {
                "id": "MOD-1",
                "name": "Board API",
                "responsibility": "Serves tasks.",
                "path": "src/api/",
                "depends_on": ["MOD-2"],
                "covers": ["REQ-1"],
            },
            {
                "id": "MOD-2",
                "name": "Store",
                "responsibility": "Keeps tasks.",
                "path": "src/store/",
                "depends_on": [],
                "covers": [],
            },
            {
                "id": "MOD-3",
                "name": "Web client",
                "responsibility": "Renders.",
                "path": "web/",
                "depends_on": ["MOD-1"],
                "covers": ["REQ-1"],
            },
        ],
        "interfaces": [
            {
                "id": "IF-1",
                "name": "Tasks HTTP API",
                "kind": "http",
                "module_id": "MOD-1",
                "contract": "GET /tasks",
                "consumers": ["MOD-3"],
            },
            {
                "id": "IF-2",
                "name": "Public API",
                "kind": "http",
                "module_id": "MOD-1",
                "contract": "GET /public",
                "consumers": [],
            },
        ],
        "decisions": [
            {
                "id": "ADR-1",
                "title": "SQLite",
                "context": "",
                "decision": "Use SQLite.",
                "consequences": "",
            }
        ],
        "conventions": [],
        "sha256": "cd" * 32,
    }
    return {
        "project": {"company_id": 1, "name": "Acme"},
        "specification": spec,
        "architecture": arch,
        "stack": {
            "discovery_id": 9,
            "language": "Python",
            "frameworks": [],
            "databases": ["postgresql"],
            "infrastructure": ["docker"],
            "sha256": "ef" * 32,
        },
        "sprints": [
            {
                "id": 7,
                "name": "Sprint one",
                "goal": "List",
                "status": "completed",
                "start_date": "2026-11-03",
                "specification_id": 3,
                "team": [],
                "first_event_at": "2026-11-03T09:00:00",
                "last_event_at": "2026-11-04T09:00:00",
                "tasks": [
                    {
                        "id": 41,
                        "title": "Build list endpoint",
                        "status": "done",
                        "assignee": "Developer",
                        "priority": "high",
                        "covers": ["REQ-1.1"],
                        "kind": None,
                    },
                    {
                        "id": 42,
                        "title": "Set up CI",
                        "status": "blocked",
                        "assignee": "DevOps",
                        "priority": "medium",
                        "covers": [],
                        "kind": None,
                    },
                ],
            }
        ],
        "events": [
            {
                "id": 100,
                "task_id": 42,
                "sprint_id": 7,
                "kind": "blocked",
                "actor": "QA",
                "created_at": "2026-11-04T08:00:00",
                "chain_hash": "unsigned:" + "1" * 64,
                "reason": "Tests fail on CI.",
            },
            {
                "id": 101,
                "task_id": 42,
                "sprint_id": 7,
                "kind": "escalated",
                "actor": "system",
                "created_at": "2026-11-04T08:05:00",
                "chain_hash": "unsigned:" + "2" * 64,
                "reason": "Blocked twice.",
            },
            {
                "id": 102,
                "task_id": 42,
                "sprint_id": 7,
                "kind": "carried_over",
                "actor": "system",
                "created_at": "2026-11-04T12:00:00",
                "chain_hash": "unsigned:" + "3" * 64,
                "reason": "Carried to sprint 8.",
                "direction": "to",
            },
        ],
        "verifications": [
            {
                "verification_id": 12,
                "run_id": 30,
                "task_id": 41,
                "verdict": "verified",
                "tier": "high",
                "reasons": ["auth path src/auth.py"],
                "artifact_sha256": "9" * 64,
                "observed_at": "2026-11-03T12:00:00",
            },
            {
                "verification_id": 13,
                "run_id": 31,
                "task_id": 42,
                "verdict": "refuted",
                "tier": "medium",
                "reasons": [],
                "artifact_sha256": "8" * 64,
                "observed_at": "2026-11-04T07:00:00",
            },
        ],
        "history": [
            {
                "kind": "specification",
                "id": 2,
                "version": 1,
                "status": "superseded",
                "supersedes_id": None,
                "created_at": "2026-10-30T10:00:00",
                "approved_at": "2026-10-30T11:00:00",
                "requirement_ids": ["REQ-1"],
            },
            {
                "kind": "specification",
                "id": 3,
                "version": 2,
                "status": "approved",
                "supersedes_id": 2,
                "created_at": "2026-10-31T10:00:00",
                "approved_at": "2026-11-01T10:00:00",
                "requirement_ids": ["REQ-1", "REQ-2"],
            },
        ],
        "roles": ["Developer", "PO", "QA", "ScrumMaster"],
        "members": {"Owner": 1, "Manager": 2},
        "retrospectives": [
            {
                "id": 60,
                "sprint_id": 7,
                "created_at": "2026-11-04T10:00:00",
                "from_role": "ScrumMaster",
                "body": "1 of 2 tasks done; 1 blocked.",
            }
        ],
        "procedures": [
            {
                "id": 8,
                "title": "Run CI locally first",
                "role": "DevOps",
                "status": "active",
                "source_sprint_id": 7,
                "result": "kept",
            }
        ],
        "integrations": [
            {
                "id": 4,
                "sprint_id": 7,
                "status": "tests_passed",
                "commit": "a" * 40,
                "tested_commit": "b" * 40,
                "created_at": "2026-11-04T11:00:00",
                "tasks": [{"task_id": 41, "commit": "c" * 40}],
            }
        ],
    }


def _empty():
    return {
        "project": {"company_id": 1, "name": "Empty"},
        "specification": None,
        "architecture": None,
        "stack": None,
        "sprints": [],
        "events": [],
        "verifications": [],
        "history": [],
        "roles": [],
        "members": {},
        "retrospectives": [],
        "procedures": [],
        "integrations": [],
    }


def _docs(snapshot, prose=None):
    documents, diagrams = pack.build_documents(snapshot, prose)
    return {d["key"]: d for d in documents}, diagrams


# --- drift ------------------------------------------------------------------------------


class _Pack:
    def __init__(self, documents, diagrams, language="en"):
        self.documents, self.diagrams, self.language = documents, diagrams, language
