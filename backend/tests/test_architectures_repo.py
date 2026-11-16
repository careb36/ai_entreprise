"""The approved architecture written to the repository with the sha256 the
daemon reports, and refused without a project folder. The shared rows are
in ``architecture_helpers``.
"""

import asyncio

import pytest
from architecture_helpers import GOOD_ARCH, _approved_spec, _company, _Daemon
from models.architecture import Architecture
from models.company import ProjectDiscovery
from services.planning import (
    arch_author,
    arch_check,
)


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
        replies={
            "workspace.list": {
                "path": "docs/adr",
                "entries": [],
                "truncated": False,
            },
            "workspace.generate": {
                "ok": True,
                "files": [
                    {
                        "path": "docs/ARCHITECTURE.md",
                        "status": "written",
                        "sha256": "ab" * 32,
                        "bytes": 10,
                    },
                    {
                        "path": "docs/adr/0001-sqlite-for-the-store.md",
                        "status": "written",
                        "sha256": "cd" * 32,
                        "bytes": 20,
                    },
                ],
            },
        }
    )
    app.state.nats_client = daemon
    written = test_client.post(f"/v1/architectures/{arch['id']}/write")
    assert written.status_code == 200, written.text
    body = written.json()
    assert (body["written_path"], body["written_sha256"]) == (
        "docs/ARCHITECTURE.md",
        "ab" * 32,
    )
    assert [f["path"] for f in body["written_files"]] == [
        "docs/ARCHITECTURE.md",
        "docs/adr/0001-sqlite-for-the-store.md",
    ]
    assert body["written_files"][1]["sha256"] == "cd" * 32
    assert body["written_files"][1]["decision_id"] == "ADR-1"
    subjects = [subject for subject, _ in daemon.requests]
    assert subjects == ["workspace.list", "workspace.generate"]
    _, listing = daemon.requests[0]
    assert listing["path"] == "docs/adr"
    _, payload = daemon.requests[1]
    assert payload["workspace_root"] == "/home/ana/app"
    assert [f["path"] for f in payload["files"]] == [
        "docs/ARCHITECTURE.md",
        "docs/adr/0001-sqlite-for-the-store.md",
    ]
    assert payload["files"][0]["content"].startswith("# Board service")
    assert payload["files"][0]["overwrite_existing"] is True
    assert payload["files"][1]["content"].startswith("# ADR 0001: SQLite for the store")

    daemon.replies["workspace.generate"] = {
        "ok": False,
        "files": [{"path": "docs/ARCHITECTURE.md", "status": "rejected"}],
    }
    assert test_client.post(f"/v1/architectures/{arch['id']}/write").status_code == 502
    daemon.replies["workspace.list"] = RuntimeError(
        "nats: no responders available for request"
    )
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
