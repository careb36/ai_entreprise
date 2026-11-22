"""Without Git a refuted delivery is taken back out (``services/loop/refuted_undo.py``):
the real daemon's snapshot puts the files back, the undo is audited as a
Manager's is, a file another task changed since is a conflict that touches
nothing, and a verified delivery, a Git branch or an absent daemon leave the
folder as it is."""

import asyncio

import pytest
import test_delivery_undo
from models.audit import AuditLog
from services.loop import refuted_undo
from sqlmodel import select
from test_delivery_undo import _company, _Daemon, _delivered_run

project = test_delivery_undo.project


def _evidence(session, run):
    from models.agent_run import Verification

    return (
        session.exec(select(Verification).where(Verification.agent_run_id == run.id))
        .one()
        .evidence
    )


def _undo(session, company, run, verdict, evidence, nats=None):
    return asyncio.run(
        refuted_undo.undo_refuted(
            nats or _Daemon(),
            session.get_bind(),
            company_id=company.id,
            item_id=7,
            run_id=run.id,
            verdict=verdict,
            evidence=evidence,
        )
    )


def _audits(session, company):
    return session.exec(
        select(AuditLog).where(
            AuditLog.company_id == company.id,
            AuditLog.action == refuted_undo.UNDO_ACTION,
        )
    ).all()


def test_a_refuted_delivery_is_undone_and_audited(
    sqlite_session, project, monkeypatch, tmp_path
):
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "audit"))
    company = _company(sqlite_session, on=False)
    run, reply = _delivered_run(sqlite_session, company, project)
    assert (project / "app.py").read_text() == "print('new')\n"
    assert not refuted_undo.undone(sqlite_session, company.id, 7)

    comment = _undo(
        sqlite_session, company, run, "refuted", _evidence(sqlite_session, run)
    )

    assert comment.startswith(refuted_undo.UNDONE_PREFIX)
    assert "1 file(s) put back and 1 removed" in comment
    assert (project / "app.py").read_text() == "print('old')\n"
    assert not (project / "pkg/sub/new.py").exists()
    assert (project / "keep.txt").read_text() == "untouched\n"
    [audit] = _audits(sqlite_session, company)
    assert audit.result["snapshot"] == reply["snapshot"]["id"]
    assert audit.result["reason"] == "refuted"
    assert audit.interaction_id == "agent-run:task-7"
    assert refuted_undo.undone(sqlite_session, company.id, 7)


@pytest.mark.parametrize("verdict", ["verified", "unverifiable", None])
def test_a_delivery_not_refuted_stays(sqlite_session, project, verdict):
    company = _company(sqlite_session, on=False)
    run, _ = _delivered_run(sqlite_session, company, project)
    nats = _Daemon()
    assert (
        _undo(
            sqlite_session, company, run, verdict, _evidence(sqlite_session, run), nats
        )
        is None
    )
    assert nats.requests == []
    assert (project / "app.py").read_text() == "print('new')\n"


def test_a_git_branch_or_a_delivery_without_snapshot_is_left_to_git(
    sqlite_session, project
):
    company = _company(sqlite_session, on=False)
    run, _ = _delivered_run(sqlite_session, company, project)
    evidence = _evidence(sqlite_session, run)
    nats = _Daemon()
    assert (
        _undo(
            sqlite_session,
            company,
            run,
            "refuted",
            {**evidence, "git": {"branch": "b"}},
            nats,
        )
        is None
    )
    no_snapshot = {k: v for k, v in evidence.items() if k != "snapshot"}
    assert _undo(sqlite_session, company, run, "refuted", no_snapshot, nats) is None
    assert nats.requests == []
    assert refuted_undo.undo_spec(None) is None


def test_a_file_changed_since_is_a_conflict_that_touches_nothing(
    sqlite_session, project
):
    company = _company(sqlite_session, on=False)
    run, _ = _delivered_run(sqlite_session, company, project)
    (project / "app.py").write_text("print('another task')\n", encoding="utf-8")

    comment = _undo(
        sqlite_session, company, run, "refuted", _evidence(sqlite_session, run)
    )

    assert comment.startswith(refuted_undo.KEPT_PREFIX)
    assert (project / "app.py").read_text() == "print('another task')\n"
    assert (project / "pkg/sub/new.py").exists()
    assert _audits(sqlite_session, company) == []
    assert not refuted_undo.undone(sqlite_session, company.id, 7)


def test_an_absent_daemon_leaves_the_delivery_and_says_so(sqlite_session, project):
    class Absent:
        async def request(self, *args, **kwargs):
            raise TimeoutError("no answer")

    company = _company(sqlite_session, on=False)
    run, _ = _delivered_run(sqlite_session, company, project)

    comment = _undo(
        sqlite_session,
        company,
        run,
        "refuted",
        _evidence(sqlite_session, run),
        Absent(),
    )

    assert (
        comment.startswith(refuted_undo.KEPT_PREFIX)
        and "could not be undone" in comment
    )
    assert (project / "app.py").read_text() == "print('new')\n"
