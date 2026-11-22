"""Importing a company's standards from the default branch (issue #700;
``docs/archive/design/COMPANY_STANDARDS.md`` §4, §10 T2, T3, T4, T6, §12 decision 5).

Real git in ``tmp_path``: ``main`` holds the standards file, a guarded
script, a guarded folder and a baseline; a task branch, checked out, edits
the file. The daemon's ``workspace.git.read_at`` (``vscode-daemon/
git_read_at.py``) is called through a fake NATS that hands the request to
it. The import reads ``main``, hashes the bytes it received, hashes each
guard's files (whole folders, exclude globs applied, symbolic links
refused), reads the baseline at the same commit, dry-runs the in-backend
kinds and stores a draft; nothing in force changes.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pytest
from models import AuditLog, Company, CompanyStandardSet, ProjectDiscovery
from services.contracts import company_standards as cs
from services.planning import company_standards_import as importer
from services.platform import company_standards_setting as setting
from sqlmodel import select

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "vscode-daemon"))
import git_read_at
from git_workspace import GitError

FILE = b"""format = 1

[[standard]]
id = "ACME-ARCH-1"
title = "Architecture check"
rule = "The architecture script passes."
binds = ["Developer"]
kind = "command"
command = "python3 scripts/check.py"
guards = ["scripts/check.py", { path = "scripts/lib", exclude = ["**/__pycache__/**"] }]
severity = "blocking"
owner = "Architect"

[[standard]]
id = "ACME-SEC-1"
title = "No env file"
rule = "Never deliver a .env file."
binds = ["*"]
kind = "forbid_path"
files = [".env", "**/.env"]
severity = "blocking"
owner = "SecOps"

[[standard]]
id = "ACME-SIZE-1"
title = "Small files"
rule = "Files stay short."
binds = ["Developer"]
kind = "max_lines"
limit = 2
files = ["src/**/*.py"]
baseline = "sizes.json"
severity = "finding"
owner = "Architect"
"""


def git(cwd, *args):
    return subprocess.run(
        ["git", "-c", "user.name=Ana", "-c", "user.email=ana@example.com", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _write(root: Path, path: str, data: bytes) -> None:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "app"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    _write(root, cs.PATH, FILE)
    _write(root, "scripts/check.py", b"print('ok')\n")
    _write(root, "scripts/lib/a.py", b"A = 1\n")
    _write(root, "scripts/lib/b/c.py", b"C = 1\n")
    _write(root, "scripts/lib/__pycache__/a.pyc", b"\x00generated")
    _write(root, "sizes.json", b'{"src/big.py": 3}')
    _write(root, "src/big.py", b"1\n2\n3\n")
    _write(root, "src/ok.py", b"1\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "standards")
    # A task branch rewrites the file and stays checked out (T2).
    git(root, "checkout", "-q", "-b", "fabrique/task-1")
    _write(root, cs.PATH, b"format = 1\n")
    git(root, "commit", "-q", "-am", "an agent removes every standard")
    return root


class _Nats:
    """Hands each request to the daemon's handler body; records the payloads."""

    def __init__(self, tamper=None):
        self.payloads: list[dict] = []
        self.tamper = tamper

    async def request(self, subject, payload, timeout):
        assert subject == git_read_at.SUBJECT
        self.payloads.append(payload)
        try:
            reply = git_read_at.read_at(
                payload["workspace_root"],
                payload.get("base", ""),
                payload.get("commit", ""),
                payload.get("paths"),
                payload.get("folders"),
                bool(payload.get("list")),
            )
        except GitError as e:
            return {"error": {"status": 409, "message": str(e)}}
        return self.tamper(reply) if self.tamper else reply


def _company(session, root: Path, base: str | None = None) -> Company:
    company = Company(company_name="Acme", git_base_branch=base)
    session.add(company)
    session.commit()
    session.add(ProjectDiscovery(company_id=company.id, project_path=str(root)))
    session.commit()
    return company


def _import(session, company, nats):
    return asyncio.run(
        importer.import_draft(
            session, nats, company, by="manager@example.test", user_id=None
        )
    )


# -- The daemon's side --------------------------------------------------------


def test_the_daemon_reads_the_default_branch_never_the_checkout(repo):
    reply = git_read_at.read_at(str(repo), paths=[cs.PATH])
    assert reply["base"] == "main"
    assert reply["commit"] == git(repo, "rev-parse", "main")
    assert base64.b64decode(reply["files"][0]["content_b64"]) == FILE
    assert "sha256" not in reply["files"][0]


def test_the_daemon_lists_a_folder_recursively_sorted(repo):
    folder = git_read_at.read_at(str(repo), folders=["scripts/lib"])["folders"][0]
    assert [f["path"] for f in folder["files"]] == [
        "scripts/lib/__pycache__/a.pyc", "scripts/lib/a.py", "scripts/lib/b/c.py",
    ]  # fmt: skip


def test_the_daemon_reports_a_symbolic_link_and_never_follows_it(repo):
    git(repo, "checkout", "-q", "main")
    os.symlink("/etc/hostname", repo / "scripts" / "lib" / "link.py")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "a link")
    files = git_read_at.read_at(str(repo), folders=["scripts/lib"])["folders"][0][
        "files"
    ]
    link = next(f for f in files if f["path"] == "scripts/lib/link.py")
    assert link == {"path": "scripts/lib/link.py", "status": "symlink"}


@pytest.mark.parametrize(
    "kwargs,error",
    [
        ({"paths": ["../etc/passwd"]}, ValueError),
        ({"commit": "main"}, ValueError),
        ({"base": "no such"}, ValueError),
        ({"base": "absent"}, GitError),
        ({"commit": "0" * 40}, GitError),
    ],
)
def test_the_daemon_refuses_what_it_cannot_read(repo, kwargs, error):
    with pytest.raises(error):
        git_read_at.read_at(str(repo), **kwargs)


def test_without_main_or_origin_head_there_is_no_default_branch(tmp_path):
    root = tmp_path / "solo"
    root.mkdir()
    git(root, "init", "-q", "-b", "trunk")
    _write(root, "x", b"x")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "x")
    with pytest.raises(GitError, match="no default branch"):
        git_read_at.read_at(str(root), paths=["x"])
    assert git_read_at.read_at(str(root), base="trunk", paths=["x"])["base"] == "trunk"


def test_the_handler_maps_errors_to_statuses(repo):
    replies = []

    async def reply(nc, msg, body):
        replies.append(body)

    class Msg:
        pass

    for body, status in (
        ({"workspace_root": "relative"}, 400),
        ({"workspace_root": str(repo / "gone")}, 404),
        ({"workspace_root": str(repo), "base": "absent"}, 409),
    ):
        asyncio.run(git_read_at.handle_read_at(None, Msg(), reply, lambda m, b=body: b))
        assert replies[-1]["error"]["status"] == status


# -- The import ---------------------------------------------------------------


def test_the_import_stores_a_draft_from_main_with_the_backend_s_hashes(
    sqlite_session, repo
):
    company = _company(sqlite_session, repo)
    row = _import(sqlite_session, company, _Nats())
    assert row.status == "draft" and row.version == 1
    assert row.raw == FILE
    assert row.sha256 == hashlib.sha256(FILE).hexdigest()
    assert row.source_commit == git(repo, "rev-parse", "main")
    assert [s["id"] for s in row.parsed["standards"]] == [
        "ACME-ARCH-1",
        "ACME-SEC-1",
        "ACME-SIZE-1",
    ]
    assert setting.refusals_of(row) == []
    assert setting.in_force(sqlite_session, company.id) is None
    lib = row.guard_hashes["scripts/lib"]
    assert sorted(lib["files"]) == [
        "scripts/lib/a.py",
        "scripts/lib/b/c.py",
    ]  # __pycache__ excluded
    assert lib["sha256"] == cs.guard_digest(lib["files"])
    assert row.guard_hashes["scripts/check.py"]["files"] == {
        "scripts/check.py": hashlib.sha256(b"print('ok')\n").hexdigest()
    }
    assert row.baselines == {"sizes.json": {"src/big.py": 3}}
    audit = sqlite_session.exec(
        select(AuditLog).where(AuditLog.action == "company_standards_imported")
    ).one()
    assert (
        audit.result["sha256"] == row.sha256
        and audit.agent_id == "human:manager@example.test"
    )


def test_a_hash_in_the_daemon_s_reply_is_never_read(sqlite_session, repo):
    """T4: whatever hash the reply carries, the stored one is computed here."""

    def tamper(reply):
        reply["sha256"] = "0" * 64
        for entry in reply["files"]:
            entry["sha256"] = "0" * 64
        return reply

    row = _import(sqlite_session, _company(sqlite_session, repo), _Nats(tamper))
    assert row.sha256 == hashlib.sha256(FILE).hexdigest()


def test_every_later_read_names_the_same_commit(sqlite_session, repo):
    nats = _Nats()
    row = _import(sqlite_session, _company(sqlite_session, repo), nats)
    assert "commit" not in nats.payloads[0]
    assert {p["commit"] for p in nats.payloads[1:]} == {row.source_commit}


def test_the_dry_run_reports_a_standard_failing_on_main(sqlite_session, repo):
    """T6: a rule that would block every task is seen before activation."""
    git(repo, "checkout", "-q", "main")
    _write(repo, ".env", b"KEY=placeholder\n")
    _write(repo, "src/new.py", b"1\n2\n3\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "env")
    row = _import(sqlite_session, _company(sqlite_session, repo), _Nats())
    assert row.dry_run["fails_on_source"] == ["ACME-SEC-1", "ACME-SIZE-1"]
    assert row.dry_run["blocking_fails_on_source"] == ["ACME-SEC-1"]
    assert row.dry_run["not_run"] == ["ACME-ARCH-1"]


def test_a_symbolic_link_in_a_guard_is_a_refusal(sqlite_session, repo):
    git(repo, "checkout", "-q", "main")
    os.symlink("a.py", repo / "scripts" / "lib" / "alias.py")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "link")
    row = _import(sqlite_session, _company(sqlite_session, repo), _Nats())
    assert [r["code"] for r in row.import_refusals] == ["guard_unreadable"]


def test_a_missing_guard_and_an_invalid_baseline_are_refusals(sqlite_session, repo):
    git(repo, "checkout", "-q", "main")
    git(repo, "rm", "-q", "scripts/check.py")
    _write(repo, "sizes.json", b"[]")
    git(repo, "commit", "-q", "-am", "broken")
    row = _import(sqlite_session, _company(sqlite_session, repo), _Nats())
    assert sorted(r["code"] for r in row.import_refusals) == [
        "guard_missing",
        "invalid_baseline",
    ]


def test_a_refused_file_is_still_a_draft_with_its_refusals(sqlite_session, repo):
    git(repo, "checkout", "-q", "main")
    _write(repo, cs.PATH, b"format = 1\n# relaxes STD-SEC-1\n")
    git(repo, "commit", "-q", "-am", "relax")
    row = _import(sqlite_session, _company(sqlite_session, repo), _Nats())
    assert [r["code"] for r in setting.refusals_of(row)] == ["catalogue_reference"]


def test_the_company_s_base_branch_is_read_when_set(sqlite_session, repo):
    company = _company(sqlite_session, repo, base="fabrique/task-1")
    row = _import(sqlite_session, company, _Nats())
    assert row.source_base == "fabrique/task-1" and row.parsed["standards"] == []


@pytest.mark.parametrize("nats", [None, "no file"])
def test_without_the_daemon_or_the_file_no_draft_is_created(sqlite_session, repo, nats):
    if nats == "no file":
        git(repo, "checkout", "-q", "main")
        git(repo, "rm", "-q", cs.PATH)
        git(repo, "commit", "-q", "-m", "gone")
        nats = _Nats()
    company = _company(sqlite_session, repo)
    with pytest.raises(importer.ImportUnavailable) as raised:
        _import(sqlite_session, company, nats)
    assert raised.value.status == 409
    assert sqlite_session.exec(select(CompanyStandardSet)).all() == []


def test_without_a_project_folder_nothing_is_asked(sqlite_session):
    company = Company(company_name="No project")
    sqlite_session.add(company)
    sqlite_session.commit()
    nats = _Nats()
    with pytest.raises(importer.ImportUnavailable):
        _import(sqlite_session, company, nats)
    assert nats.payloads == []
