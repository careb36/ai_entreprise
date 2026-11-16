"""The foundation sprint, decided from what the daemon observed (retrospective F8).

``POST /v1/init_company`` plans "Sprint 0 — Foundations" unless the request
declines it (``foundation_sprint: false``) or the workspace daemon observed
the project folder to be a repository with code; the request may also ask for
the sprint over an observed repository (``foundation_sprint: true``). An
unobserved folder — no daemon answered, no folder named — keeps the old
behaviour and the sprint is planned. The response says which it was, with the
daemon's observation beside the decision, never merged into it: a claim in
the request (``detected_stack``) decides nothing.
"""

from __future__ import annotations

import pytest
from models import (
    Agent,
    BacklogItem,
    Company,
    CompanyMember,
    ProjectDiscovery,
    Sprint,
)
from services.loop import foundation_setup
from services.planning.stack_detection import has_code_files
from sqlmodel import func, select

PATH = "/srv/app"
AGENTS = [{"role": "PO", "name": "Pat"}, {"role": "QA", "name": "Quinn"}]

# Deleted after each test, children first: the suite shares one in-memory
# SQLite engine and the route commits through it, so rows left behind would
# shift the auto-increment ids (and feed the global scans) of the
# order-sensitive tests that run after this module. SQLite hands deleted ids
# out again, so the cleanup keeps this module state-neutral.
_CLEANED = (
    BacklogItem,
    Sprint,
    ProjectDiscovery,
    CompanyMember,
    Agent,
    Company,
)


@pytest.fixture(autouse=True)
def _leave_no_rows(sqlite_session):
    marks = {
        model: (sqlite_session.exec(select(func.max(model.id))).one() or 0)
        for model in _CLEANED
    }
    yield
    for model in _CLEANED:
        for row in sqlite_session.exec(
            select(model).where(model.id > marks[model])
        ).all():
            sqlite_session.delete(row)
    sqlite_session.commit()


SNAPSHOT_WITH_CODE = {
    "files": ["README.md", "backend/app/main.py", "frontend/src/app.tsx"],
    "contents": {},
    "truncated": False,
}
SNAPSHOT_DOCS_ONLY = {
    "files": ["README.md", "LICENSE"],
    "contents": {},
    "truncated": False,
}
SNAPSHOT_EMPTY: dict = {"files": [], "contents": {}, "truncated": False}


def _project(is_repo: bool, snapshot: dict | None) -> dict:
    out: dict = {
        "host": {"hostname": "test-host"},
        "path": PATH,
        "name": "app",
        "exists": True,
        "is_dir": True,
        "git": {"is_repo": is_repo},
    }
    if snapshot is not None:
        out["snapshot"] = snapshot
    return out


class _InspectingNATS:
    """The daemon's inspection of the folder, as the route asks it."""

    def __init__(self, project: dict | None):
        self.project = project
        self.subjects: list[tuple[str, dict]] = []

    async def request(self, subject, payload, timeout=5.0):
        self.subjects.append((subject, payload))
        if subject == "workspace.inspect":
            return self.project
        raise TimeoutError(subject)

    async def close(self):
        pass


class _DownNATS:
    """A daemon that never answers."""

    async def request(self, subject, payload, timeout=5.0):
        raise TimeoutError("no answer in the test")

    async def close(self):
        pass


def _init(test_client, nats, **body) -> dict:
    test_client.app.state.nats_client = nats
    response = test_client.post(
        "/v1/init_company",
        json={
            "company_name": "Folder Corp",
            "project_path": PATH,
            "agents": AGENTS,
            "start_now": False,
            **body,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


# --- the route: the observation decides ---------------------------------------


def test_an_observed_repository_with_code_gets_no_foundation_sprint(
    test_client, sqlite_session
):
    nats = _InspectingNATS(_project(True, SNAPSHOT_WITH_CODE))
    body = _init(test_client, nats)

    assert body["sprint_id"] is None
    assert body["sprint_status"] is None
    assert body["task_count"] == 0
    assert body["foundation_sprint"] == {
        "planned": False,
        "reason": "repository_with_code",
        "detail": "skipped: the daemon observed an existing repository with code",
        "observed_repository_with_code": True,
    }
    # The company exists; only the sprint does not. (The SQLite engine is
    # shared across tests, so every query here is scoped to this company.)
    company_id = body["company_id"]
    assert sqlite_session.get(Company, company_id) is not None
    assert (
        sqlite_session.exec(select(Sprint).where(Sprint.company_id == company_id)).all()
        == []
    )
    assert (
        sqlite_session.exec(
            select(BacklogItem).where(
                BacklogItem.sprint_id.in_(  # type: ignore[attr-defined]
                    select(Sprint.id).where(Sprint.company_id == company_id)
                )
            )
        ).all()
        == []
    )
    discovery = sqlite_session.exec(
        select(ProjectDiscovery).where(ProjectDiscovery.company_id == company_id)
    ).one()
    assert discovery.project_path == PATH
    # One inspection, with the snapshot the observation reads.
    assert nats.subjects == [("workspace.inspect", {"path": PATH, "snapshot": True})]


def test_an_empty_folder_keeps_the_foundation_sprint(test_client, sqlite_session):
    body = _init(test_client, _InspectingNATS(_project(False, SNAPSHOT_EMPTY)))
    assert body["sprint_id"] is not None
    assert body["task_count"] > 0
    assert body["foundation_sprint"]["planned"] is True
    assert body["foundation_sprint"]["reason"] == "folder_without_code"
    assert body["foundation_sprint"]["observed_repository_with_code"] is False
    sprint = sqlite_session.get(Sprint, body["sprint_id"])
    assert sprint is not None and sprint.status == "planned"


def test_a_repository_without_code_files_keeps_the_foundation_sprint(
    test_client, sqlite_session
):
    # A fresh repository (a README and a licence) is still a project to found.
    body = _init(test_client, _InspectingNATS(_project(True, SNAPSHOT_DOCS_ONLY)))
    assert body["sprint_id"] is not None
    assert body["foundation_sprint"]["reason"] == "folder_without_code"
    assert body["foundation_sprint"]["observed_repository_with_code"] is False


def test_an_explicit_choice_overrides_the_observation(test_client, sqlite_session):
    asked = _init(
        test_client,
        _InspectingNATS(_project(True, SNAPSHOT_WITH_CODE)),
        foundation_sprint=True,
    )
    assert asked["sprint_id"] is not None
    assert asked["foundation_sprint"]["planned"] is True
    assert asked["foundation_sprint"]["reason"] == "requested"
    # The decision and the observation stay apart: asking does not rewrite
    # what the daemon saw.
    assert asked["foundation_sprint"]["observed_repository_with_code"] is True

    declined = _init(
        test_client,
        _InspectingNATS(_project(False, SNAPSHOT_EMPTY)),
        foundation_sprint=False,
    )
    assert declined["sprint_id"] is None
    assert declined["foundation_sprint"]["planned"] is False
    assert declined["foundation_sprint"]["reason"] == "declined"
    assert declined["foundation_sprint"]["observed_repository_with_code"] is False


def test_a_claim_in_the_request_never_skips_the_sprint(test_client, sqlite_session):
    # detected_stack is the request's own word; the folder was observed empty.
    body = _init(
        test_client,
        _InspectingNATS(_project(True, SNAPSHOT_EMPTY)),
        detected_stack={
            "language": "Python",
            "file_count": 900,
            "frameworks": ["FastAPI"],
        },
    )
    assert body["sprint_id"] is not None
    assert body["foundation_sprint"]["reason"] == "folder_without_code"
    assert body["foundation_sprint"]["observed_repository_with_code"] is False


def test_an_unreachable_daemon_keeps_today_s_behaviour_and_says_so(
    test_client, sqlite_session
):
    body = _init(test_client, _DownNATS())
    assert body["sprint_id"] is not None
    assert body["task_count"] > 0
    outcome = body["foundation_sprint"]
    assert (outcome["planned"], outcome["reason"]) == (True, "daemon_unobserved")
    assert outcome["observed_repository_with_code"] is None
    assert "daemon" in outcome["detail"]


def test_no_folder_to_observe_plans_the_sprint(test_client, sqlite_session):
    nats = _InspectingNATS(_project(True, SNAPSHOT_WITH_CODE))
    test_client.app.state.nats_client = nats
    response = test_client.post(
        "/v1/init_company",
        json={"company_name": "No Folder Corp", "agents": AGENTS, "start_now": False},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["sprint_id"] is not None
    assert body["foundation_sprint"]["reason"] == "no_folder"
    assert body["foundation_sprint"]["observed_repository_with_code"] is None
    assert nats.subjects == []  # nothing to inspect


def test_a_daemon_reply_without_a_snapshot_never_skips_the_sprint(
    test_client, sqlite_session
):
    # An older daemon that ignores the snapshot flag: unknown is not "no code".
    body = _init(test_client, _InspectingNATS(_project(True, None)))
    assert body["sprint_id"] is not None
    assert body["foundation_sprint"]["reason"] == "folder_without_code"
    assert body["foundation_sprint"]["observed_repository_with_code"] is False


# --- the pure pieces ------------------------------------------------------------


@pytest.mark.parametrize(
    ("paths", "expected"),
    [
        ([], False),
        (["README.md", "LICENSE", "docs/guide.rst"], False),
        (["src/main.py"], True),
        (["web/app.tsx", "package.json"], True),
        (["Makefile", "run.sh"], False),
        (["vendor/lib.go"], True),  # the snapshot's skip-dirs already pruned it
    ],
)
def test_code_files_are_the_extension_map(paths, expected):
    assert has_code_files(paths) is expected


@pytest.mark.parametrize(
    ("state", "observed", "requested", "planned", "reason"),
    [
        ("observed", True, None, False, "repository_with_code"),
        ("observed", False, None, True, "folder_without_code"),
        ("observed", True, True, True, "requested"),
        ("observed", False, True, True, "requested"),
        ("observed", True, False, False, "declined"),
        ("observed", False, False, False, "declined"),
        ("no_folder", None, None, True, "no_folder"),
        ("unobserved", None, None, True, "daemon_unobserved"),
        ("no_folder", None, True, True, "requested"),
        ("unobserved", None, False, False, "declined"),
    ],
)
def test_the_decision_table(state, observed, requested, planned, reason):
    outcome = foundation_setup.decide(
        {"state": state, "repository_with_code": observed}, requested
    )
    assert outcome["planned"] is planned
    assert outcome["reason"] == reason
    assert outcome["observed_repository_with_code"] is observed
    assert outcome["detail"]
