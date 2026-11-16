"""The architecture's ADR files are written with it (issue #329, finding F4
of ``docs/retrospectives/2026-11-self-hosting.md``).

The first free number is read from the repository through the daemon, a
decision already written keeps its file and number, a superseded decision
says so in its file, every file's sha256 is the daemon's observation, and a
missing daemon writes nothing. What the listing cannot answer — no ``docs/adr``
yet, a listing the daemon truncated, a project folder it cannot read — is
reported, never guessed. The write itself is faked in memory, like the other
daemon tests (``test_architectures._Daemon``).
"""

from __future__ import annotations

import hashlib

from models.company import ProjectDiscovery
from test_architectures import (
    GOOD_ARCH,
    _approved_spec,
    _company,
    _Daemon,
)

from app.main import app


class _RepositoryDaemon(_Daemon):
    """The existing fake, with a ``docs/adr`` folder the write updates."""

    def __init__(self, files=None, hashes=None, list_error=None):
        super().__init__()
        self.files = dict(files or {})
        self.hashes = dict(hashes or {})
        self.list_error = list_error

    async def request(self, subject, payload, timeout=5.0):
        self.requests.append((subject, payload))
        if subject == "workspace.list":
            if self.list_error is not None:
                raise self.list_error
            folder = str(payload.get("path") or ".")
            prefix = folder.rstrip("/") + "/"
            return {
                "path": folder,
                "entries": sorted(p for p in self.files if p.startswith(prefix)),
                "truncated": False,
            }
        if subject == "workspace.generate":
            out = []
            for item in payload.get("files") or []:
                content = str(item.get("content") or "")
                self.files[str(item["path"])] = content
                data = content.encode()
                out.append(
                    {
                        "path": item["path"],
                        "status": "written",
                        "sha256": self.hashes.get(item["path"])
                        or hashlib.sha256(data).hexdigest(),
                        "bytes": len(data),
                    }
                )
            return {"ok": True, "files": out}
        return {"ok": False, "files": []}


def _approved_architecture(sqlite_session, test_client, decisions=None):
    company = _company(sqlite_session)
    spec = _approved_spec(sqlite_session, company)
    sqlite_session.add(
        ProjectDiscovery(company_id=company.id, project_path="/home/ana/app")
    )
    sqlite_session.commit()
    body = {**GOOD_ARCH}
    if decisions is not None:
        body["decisions"] = decisions
    arch = test_client.post(
        "/v1/architectures", json={"specification_id": spec.id, **body}
    ).json()
    approved = test_client.post(f"/v1/architectures/{arch['id']}/approve")
    assert approved.status_code == 200, approved.text
    return arch


def _generate_payload(daemon):
    return next(
        payload
        for subject, payload in daemon.requests
        if subject == "workspace.generate"
    )


def test_the_first_write_numbers_the_adrs_from_the_repositorys_first_free_number(
    sqlite_session, test_client
):
    arch = _approved_architecture(sqlite_session, test_client)
    daemon = _RepositoryDaemon(
        files={
            "docs/adr/0007-legacy-decision.md": "# ADR 0007\n",
            "docs/adr/0009-another-one.md": "# ADR 0009\n",
        }
    )
    app.state.nats_client = daemon
    written = test_client.post(f"/v1/architectures/{arch['id']}/write")
    assert written.status_code == 200, written.text
    adrs = [f for f in written.json()["written_files"] if f["kind"] == "adr"]
    assert [(f["number"], f["path"]) for f in adrs] == [
        (10, "docs/adr/0010-sqlite-for-the-store.md")
    ]
    assert daemon.files["docs/adr/0007-legacy-decision.md"] == "# ADR 0007\n"
    assert "docs/adr/0010-sqlite-for-the-store.md" in daemon.files


def test_a_second_write_keeps_the_numbers_and_writes_the_same_bytes(
    sqlite_session, test_client
):
    arch = _approved_architecture(sqlite_session, test_client)
    daemon = _RepositoryDaemon()
    app.state.nats_client = daemon
    first = test_client.post(f"/v1/architectures/{arch['id']}/write")
    assert first.status_code == 200, first.text
    first_files = {f["path"]: f["content"] for f in _generate_payload(daemon)["files"]}
    assert "docs/adr/0001-sqlite-for-the-store.md" in first_files

    daemon.requests.clear()
    second = test_client.post(f"/v1/architectures/{arch['id']}/write")
    assert second.status_code == 200, second.text
    second_files = {f["path"]: f["content"] for f in _generate_payload(daemon)["files"]}
    assert second_files == first_files
    assert first.json()["written_files"] == second.json()["written_files"]


def test_a_superseded_decision_says_so_in_its_file(sqlite_session, test_client):
    decisions = [
        {
            "title": "SQLite for the store",
            "context": "A single small team.",
            "decision": "Use SQLite through the standard library.",
            "consequences": "No server to run.",
            "slug": "sqlite-for-the-store",
            "superseded_by": "ADR-2",
        },
        {
            "title": "Postgres for the store",
            "context": "The team grows.",
            "decision": "Use Postgres.",
            "consequences": "A server to run.",
            "slug": "postgres-for-the-store",
        },
    ]
    arch = _approved_architecture(sqlite_session, test_client, decisions=decisions)
    daemon = _RepositoryDaemon()
    app.state.nats_client = daemon
    written = test_client.post(f"/v1/architectures/{arch['id']}/write")
    assert written.status_code == 200, written.text
    old = daemon.files["docs/adr/0001-sqlite-for-the-store.md"]
    new = daemon.files["docs/adr/0002-postgres-for-the-store.md"]
    assert "Superseded by ADR 0002." in old
    assert "Accepted (" not in old and "Proposed." not in old
    assert "Accepted (" in new


def test_the_hashes_kept_are_the_daemons_reply(sqlite_session, test_client):
    arch = _approved_architecture(sqlite_session, test_client)
    adr_path = "docs/adr/0001-sqlite-for-the-store.md"
    daemon = _RepositoryDaemon(
        hashes={"docs/ARCHITECTURE.md": "ab" * 32, adr_path: "cd" * 32}
    )
    app.state.nats_client = daemon
    written = test_client.post(f"/v1/architectures/{arch['id']}/write")
    assert written.status_code == 200, written.text
    body = written.json()
    assert body["written_sha256"] == "ab" * 32
    files = {f["path"]: f for f in body["written_files"]}
    assert files[adr_path]["sha256"] == "cd" * 32
    assert hashlib.sha256(daemon.files[adr_path].encode()).hexdigest() != "cd" * 32
    stored = test_client.get(f"/v1/architectures/{arch['id']}").json()
    assert stored["written_files"] == body["written_files"]


def test_without_a_daemon_the_write_is_a_clear_error_and_nothing_is_written(
    sqlite_session, test_client
):
    arch = _approved_architecture(sqlite_session, test_client)
    daemon = _RepositoryDaemon(
        list_error=RuntimeError("nats: no responders available for request")
    )
    app.state.nats_client = daemon
    written = test_client.post(f"/v1/architectures/{arch['id']}/write")
    assert written.status_code == 503
    detail = written.json()["detail"]
    assert "No workspace daemon answered" in detail and "no responders" in detail
    assert [subject for subject, _ in daemon.requests] == ["workspace.list"]
    assert daemon.files == {}
    stored = test_client.get(f"/v1/architectures/{arch['id']}").json()
    assert stored["written_path"] is None
    assert stored["written_files"] == []


def test_a_repository_without_an_adr_folder_numbers_from_one(
    sqlite_session, test_client
):
    arch = _approved_architecture(sqlite_session, test_client)
    daemon = _Daemon(
        replies={
            "workspace.list": {
                "error": {"status": 404, "message": "Not a folder: docs/adr"}
            },
            "workspace.generate": {
                "ok": True,
                "files": [
                    {
                        "path": "docs/ARCHITECTURE.md",
                        "status": "written",
                        "sha256": "ab" * 32,
                    },
                    {
                        "path": "docs/adr/0001-sqlite-for-the-store.md",
                        "status": "written",
                        "sha256": "cd" * 32,
                    },
                ],
            },
        }
    )
    app.state.nats_client = daemon
    written = test_client.post(f"/v1/architectures/{arch['id']}/write")
    assert written.status_code == 200, written.text
    assert [(f["kind"], f["path"]) for f in written.json()["written_files"]] == [
        ("architecture", "docs/ARCHITECTURE.md"),
        ("adr", "docs/adr/0001-sqlite-for-the-store.md"),
    ]


def test_a_truncated_adr_listing_is_refused_rather_than_a_guessed_number(
    sqlite_session, test_client
):
    arch = _approved_architecture(sqlite_session, test_client)
    daemon = _Daemon(
        replies={
            "workspace.list": {
                "path": "docs/adr",
                "entries": ["docs/adr/0400-x.md"],
                "truncated": True,
            }
        }
    )
    app.state.nats_client = daemon
    written = test_client.post(f"/v1/architectures/{arch['id']}/write")
    assert written.status_code == 502
    assert "truncated" in written.json()["detail"]
    assert [subject for subject, _ in daemon.requests] == ["workspace.list"]


def test_a_project_folder_the_daemon_cannot_read_is_not_an_empty_adr_folder(
    sqlite_session, test_client
):
    arch = _approved_architecture(sqlite_session, test_client)
    daemon = _Daemon(
        replies={
            "workspace.list": {
                "error": {
                    "status": 404,
                    "message": "The project folder is not on this machine.",
                }
            }
        }
    )
    app.state.nats_client = daemon
    written = test_client.post(f"/v1/architectures/{arch['id']}/write")
    assert written.status_code == 404
    assert "not on this machine" in written.json()["detail"]
    assert [subject for subject, _ in daemon.requests] == ["workspace.list"]
