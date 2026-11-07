"""The architecture: modules, interfaces, decisions and conventions as data,
numbered and checked deterministically, drafted by the Architect from an
approved specification, approved by a person, in every task's prompt and
written to the repository with the sha256 the daemon reports
(``services.arch_check``, ``arch_author``, ``routers/architectures.py``).
"""

import asyncio
import json
import secrets

import pytest
from models import BacklogItem, Company, Sprint
from models.architecture import Architecture
from models.company import ProjectDiscovery
from models.specification import Specification
from services import arch_author, arch_check, spec_author, spec_check, spec_planning
from test_git_workflow import _member, enforcement  # noqa: F401
from test_specifications import GOOD

GOOD_ARCH = {
    "title": "Board service",
    "overview": "An API serves the board; a web client renders it.",
    "modules": [
        {
            "name": "Board API",
            "responsibility": "Lists and moves tasks over HTTP.",
            "path": "src/api/",
            "depends_on": ["Store"],
            "covers": ["REQ-1", "REQ-2"],
        },
        {
            "name": "Store",
            "responsibility": "Keeps the tasks and their status.",
            "path": "src/store/",
            "depends_on": [],
            "covers": [],
        },
        {
            "name": "Web client",
            "responsibility": "Renders the board and drags tasks.",
            "path": "web/",
            "depends_on": ["Board API"],
            "covers": ["REQ-1"],
        },
    ],
    "interfaces": [
        {
            "name": "Tasks HTTP API",
            "kind": "http",
            "module": "Board API",
            "contract": "GET /tasks -> [{id, title, status}]\nPATCH /tasks/{id} {status}",
            "consumers": ["Web client"],
        },
    ],
    "decisions": [
        {
            "title": "SQLite for the store",
            "context": "A single small team.",
            "decision": "Use SQLite through the standard library.",
            "consequences": "No server to run; one writer at a time.",
        },
    ],
    "conventions": ["Tests live beside the module they cover."],
}


def _company(session):
    company = Company(company_name=f"Arch {secrets.token_hex(3)}", autonomy_level=2)
    session.add(company)
    session.commit()
    return company


def _approved_spec(session, company):
    spec = Specification(
        company_id=company.id, status="approved", **spec_check.normalize(GOOD)
    )
    session.add(spec)
    session.commit()
    return spec


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


# --- the routes --------------------------------------------------------------


@pytest.mark.usefixtures("enforcement")
def test_a_manager_writes_a_draft_a_viewer_reads_it_and_nobody_else(
    sqlite_session, test_client
):
    company, other = _company(sqlite_session), _company(sqlite_session)
    spec = _approved_spec(sqlite_session, company)
    manager = _member(sqlite_session, company.id, "Manager")
    viewer = _member(sqlite_session, company.id, "Viewer")
    outsider = _member(sqlite_session, other.id, "Manager")
    body = {"specification_id": spec.id, **GOOD_ARCH}
    assert (
        test_client.post("/v1/architectures", json=body, headers=viewer).status_code
        == 403
    )
    assert (
        test_client.post("/v1/architectures", json=body, headers=outsider).status_code
        == 403
    )
    created = test_client.post("/v1/architectures", json=body, headers=manager)
    assert created.status_code == 201, created.text
    arch = created.json()
    assert arch["status"] == "draft" and arch["check"]["ok"] is True
    assert arch["interfaces"][0]["module_id"] == "MOD-1"
    listed = test_client.get(
        f"/v1/architectures?specification_id={spec.id}", headers=viewer
    ).json()
    assert [a["id"] for a in listed["architectures"]] == [arch["id"]]
    assert (
        test_client.get(f"/v1/architectures/{arch['id']}", headers=outsider).status_code
        == 403
    )
    document = test_client.get(
        f"/v1/architectures/{arch['id']}/document", headers=viewer
    )
    assert document.status_code == 200 and document.text.startswith("# Board service")


@pytest.mark.usefixtures("enforcement")
def test_the_architect_drafts_from_the_approved_specification_asks_and_a_person_approves(
    sqlite_session, test_client, monkeypatch
):
    company = _company(sqlite_session)
    spec = _approved_spec(sqlite_session, company)
    draft_spec = Specification(company_id=company.id, **spec_check.normalize(GOOD))
    sqlite_session.add(draft_spec)
    sqlite_session.add(
        ProjectDiscovery(
            company_id=company.id,
            project_path="/nowhere/app",
            detected_stack=json.dumps({"language": "Python"}),
        )
    )
    sqlite_session.commit()
    manager = _member(sqlite_session, company.id, "Manager")
    calls = []

    def fake_ask(model, system_prompt, prompt, **kwargs):
        calls.append((kwargs.get("role"), prompt))
        draft = {**GOOD_ARCH, "questions": ["Which database?"]}
        if "Answers to your questions" not in prompt:
            draft["decisions"] = [
                {"title": "Pick a store", "context": "c", "decision": ""}
            ]
        return json.dumps(draft)

    monkeypatch.setattr(spec_author, "ask", fake_ask)
    # The Architect's call runs in a thread without the session: what it needs
    # is plain values, read before.
    prepared = arch_author.prepare(sqlite_session, spec)
    assert set(prepared) == {
        "specification_id",
        "model",
        "system_prompt",
        "prompt",
        "bu_id",
        "questions",
    }
    assert prepared["specification_id"] == spec.id and "REQ-1.1" in prepared["prompt"]
    assert (
        test_client.post(
            "/v1/architectures/draft",
            json={"specification_id": draft_spec.id},
            headers=manager,
        ).status_code
        == 409
    )
    drafted = test_client.post(
        "/v1/architectures/draft", json={"specification_id": spec.id}, headers=manager
    )
    assert drafted.status_code == 200, drafted.text
    arch = drafted.json()
    assert arch["drafted_by"] == "Architect" and calls[0][0] == "Architect"
    assert (
        "REQ-2.1: given a task in todo" in calls[0][1]
        and "Primary language: Python" in calls[0][1]
    )
    blocking = {
        (f["where"], f["code"])
        for f in arch["check"]["findings"]
        if f["severity"] == "blocking"
    }
    assert blocking == {("ADR-1", "no_decision"), ("Q1", "open_question")}
    refused = test_client.post(
        f"/v1/architectures/{arch['id']}/approve", headers=manager
    )
    assert refused.status_code == 409 and [
        f["where"] for f in refused.json()["detail"]["findings"]
    ] == ["ADR-1", "Q1"]

    answered = test_client.post(
        f"/v1/architectures/{arch['id']}/questions/0",
        json={"answer": "SQLite"},
        headers=manager,
    ).json()
    assert answered["questions"] == [
        {"question": "Which database?", "answer": "SQLite"}
    ]
    revised = test_client.post(
        "/v1/architectures/draft",
        json={"specification_id": spec.id, "architecture_id": arch["id"]},
        headers=manager,
    ).json()
    assert revised["id"] == arch["id"] and "A: SQLite" in calls[1][1]
    assert (
        revised["check"]["ok"] is True and revised["questions"][0]["answer"] == "SQLite"
    )
    approved = test_client.post(
        f"/v1/architectures/{arch['id']}/approve", headers=manager
    ).json()
    assert approved["status"] == "approved"
    assert (
        test_client.put(
            f"/v1/architectures/{arch['id']}", json=GOOD_ARCH, headers=manager
        ).status_code
        == 409
    )

    monkeypatch.setattr(spec_author, "ask", lambda *a, **k: "no json here")
    assert (
        test_client.post(
            "/v1/architectures/draft",
            json={"specification_id": spec.id},
            headers=manager,
        ).status_code
        == 502
    )
    assert (
        len(
            test_client.get(
                f"/v1/architectures?specification_id={spec.id}", headers=manager
            ).json()["architectures"]
        )
        == 1
    )


@pytest.mark.usefixtures("enforcement")
def test_a_revision_supersedes_the_approved_one_and_the_prompts_follow_it(
    sqlite_session, test_client
):
    company = _company(sqlite_session)
    spec = _approved_spec(sqlite_session, company)
    manager = _member(sqlite_session, company.id, "Manager")
    v1 = test_client.post(
        "/v1/architectures",
        json={"specification_id": spec.id, **GOOD_ARCH},
        headers=manager,
    ).json()
    assert (
        test_client.post(
            f"/v1/architectures/{v1['id']}/revise", headers=manager
        ).status_code
        == 409
    )
    test_client.post(f"/v1/architectures/{v1['id']}/approve", headers=manager)
    v2 = test_client.post(f"/v1/architectures/{v1['id']}/revise", headers=manager)
    assert v2.status_code == 201 and (
        v2.json()["version"],
        v2.json()["supersedes_id"],
    ) == (2, v1["id"])
    assert (
        test_client.post(
            f"/v1/architectures/{v1['id']}/revise", headers=manager
        ).status_code
        == 409
    )
    edited = test_client.put(
        f"/v1/architectures/{v2.json()['id']}",
        json={**GOOD_ARCH, "title": "Board service v2"},
        headers=manager,
    ).json()
    test_client.post(f"/v1/architectures/{edited['id']}/approve", headers=manager)
    statuses = {
        a["id"]: a["status"]
        for a in test_client.get(
            f"/v1/architectures?specification_id={spec.id}", headers=manager
        ).json()["architectures"]
    }
    assert statuses == {v1["id"]: "superseded", edited["id"]: "approved"}

    sprint = Sprint(company_id=company.id, sprint_name="S", specification_id=spec.id)
    sqlite_session.add(sprint)
    sqlite_session.commit()
    item = BacklogItem(sprint_id=sprint.id, title="Move", covers=["REQ-2.1"])
    sqlite_session.add(item)
    sqlite_session.commit()
    context = spec_planning.requirements_context(sqlite_session.get_bind(), item.id)
    assert (
        "## Requirements this task covers" in context and "## Architecture" in context
    )
    assert "MOD-1: Board API" in context and "MOD-3: Web client" not in context
    assert "Board service v2" not in context  # the title is not what a task needs
    assert arch_author.planning_context(sqlite_session, spec.id).startswith(
        "\n\n## Architecture (approved)"
    )


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


class _Daemon:
    jetstream_enabled = False

    def __init__(self, reply):
        self.reply = reply
        self.requests = []

    async def close(self):
        pass

    async def request(self, subject, payload, timeout=5.0):
        self.requests.append((subject, payload))
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def test_the_approved_architecture_is_written_to_the_repository_with_the_daemons_hash(
    sqlite_session, test_client
):
    from app.main import app

    company = _company(sqlite_session)
    spec = _approved_spec(sqlite_session, company)
    sqlite_session.add(
        ProjectDiscovery(company_id=company.id, project_path="/home/ana/app")
    )
    sqlite_session.commit()
    arch = test_client.post(
        "/v1/architectures", json={"specification_id": spec.id, **GOOD_ARCH}
    ).json()
    assert test_client.post(f"/v1/architectures/{arch['id']}/write").status_code == 409
    test_client.post(f"/v1/architectures/{arch['id']}/approve")

    daemon = _Daemon(
        {
            "ok": True,
            "files": [
                {
                    "path": "docs/ARCHITECTURE.md",
                    "status": "written",
                    "sha256": "ab" * 32,
                    "bytes": 10,
                }
            ],
        }
    )
    app.state.nats_client = daemon
    written = test_client.post(f"/v1/architectures/{arch['id']}/write")
    assert written.status_code == 200, written.text
    assert (written.json()["written_path"], written.json()["written_sha256"]) == (
        "docs/ARCHITECTURE.md",
        "ab" * 32,
    )
    subject, payload = daemon.requests[0]
    assert (
        subject == "workspace.generate" and payload["workspace_root"] == "/home/ana/app"
    )
    assert payload["files"][0]["path"] == "docs/ARCHITECTURE.md" and payload["files"][
        0
    ]["content"].startswith("# Board service")
    assert payload["files"][0]["overwrite_existing"] is True

    daemon.reply = {
        "ok": False,
        "files": [{"path": "docs/ARCHITECTURE.md", "status": "rejected"}],
    }
    assert test_client.post(f"/v1/architectures/{arch['id']}/write").status_code == 502
    daemon.reply = RuntimeError("nats: no responders available for request")
    assert test_client.post(f"/v1/architectures/{arch['id']}/write").status_code == 503


def test_writing_needs_a_project_folder(sqlite_session):
    company = _company(sqlite_session)
    spec = _approved_spec(sqlite_session, company)
    arch = Architecture(
        company_id=company.id,
        specification_id=spec.id,
        status="approved",
        **arch_check.normalize(GOOD_ARCH),
    )
    sqlite_session.add(arch)
    sqlite_session.commit()
    with pytest.raises(arch_author.ArchAuthorError, match="no project folder"):
        asyncio.run(arch_author.write_document(_Daemon({}), sqlite_session, arch, 1.0))
