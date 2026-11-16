"""What the tests of the project wiki in the loop share (issue #445): a project
with a sprint, its specification and architecture, and the verified runs the
wiki compiles from. The tests are ``test_project_wiki_loop.py``,
``test_project_wiki_prompt.py`` and ``test_project_wiki_api.py``.
"""

from __future__ import annotations

from uuid import uuid4

from models import (
    AgentRun,
    Architecture,
    BacklogItem,
    Company,
    Specification,
    Sprint,
    Verification,
)
from models.agent_run import TASK_WORKER_PRODUCER

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
