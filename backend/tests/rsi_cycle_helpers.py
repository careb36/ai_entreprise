"""Shared set-up for the self-improvement cycle's tests
(``services/management/rsi_cycle.py``)."""

from __future__ import annotations

import json

import factories
import pytest
from config import settings
from models import AuditLog
from services.learning import findings
from services.management import rsi_cycle, rsi_cycle_steps
from sqlmodel import select

B = "b" * 40
C = "c" * 40
SPEC = {
    "title": "The lint after an edit runs the edited file's folder's linter",
    "target_measure": "scripts/self_host/measures/test_folder_lint.py",
}


@pytest.fixture
def rsi_on(monkeypatch, tmp_path):
    """The loop on, with a known first sprint, and the audit in a tmp folder."""
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "audit"))
    monkeypatch.setattr(settings, "RSI_ENABLED", True)
    monkeypatch.setattr(settings, "RSI_FIRST_SPRINT_TOKENS", 1000)
    monkeypatch.setattr(settings, "RSI_CYCLE_TOKEN_BUDGET", 0)
    monkeypatch.setattr(settings, "RSI_CYCLES_PER_DAY", 1)
    monkeypatch.setattr(settings, "RSI_STOP_AFTER_CYCLES_WITHOUT_KEEP", 3)
    monkeypatch.setattr(settings, "RSI_MAX_DIFF_FILES", 20)
    monkeypatch.setattr(settings, "RSI_MAX_DIFF_LINES", 600)
    monkeypatch.setattr(settings, "RSI_FILES_ROOT", "")


def hosting(session, autonomy: int = 2, on: bool = True):
    """A company its Owner marked as hosting the factory."""
    return factories.company(
        session, protected_paths_enabled=on, autonomy_level=autonomy
    )


def finding(session, company_id: int, n: int = 0):
    row = findings.create_from_specification(
        session,
        company_id,
        specification_text=json.dumps({**SPEC, "n": n}),
        title=SPEC["title"],
        target_measure=SPEC["target_measure"],
    )
    session.commit()
    return row


def started(session, company, *, day: int = 1, n: int = 0, **kw):
    """A cycle started on 2026-11-<day> for a new finding."""
    from datetime import datetime

    row = finding(session, company.id, n or day)
    return rsi_cycle.start(
        session, company.id, row.id, B, now=datetime(2026, 11, day, 9), **kw
    )


def diff(files: int = 3, lines_per_file: int = 10, **extra):
    """The integration's diff as ``integration_review.observed`` returns it."""
    paths = [f"backend/app/services/tools/f{i}.py" for i in range(files)]
    return {
        "files": [
            {"path": p, "additions": lines_per_file, "deletions": 0} for p in paths
        ],
        "paths": paths,
        "truncated": False,
        "paths_truncated": False,
        "diff_sha256": "d" * 64,
        **extra,
    }


def kept(session, company, *, day: int = 1):
    """A cycle decided keep, waiting for a person's merge at C."""
    cycle = started(session, company, day=day)
    rsi_cycle_steps.record_candidate(session, cycle, C)
    rsi_cycle_steps.observe_size(session, cycle, diff())
    rsi_cycle_steps.account_tokens(session, cycle, bench_tokens=100)
    return rsi_cycle_steps.record_decision(
        session, cycle, {"decision": "keep", "reasons": [], "metrics": {}}
    )


def ended_without_keep(session, company, *, day: int):
    cycle = started(session, company, day=day)
    return rsi_cycle_steps.record_decision(
        session, cycle, {"decision": "revert", "reasons": [{"code": "target"}]}
    )


def audits(session, company_id: int, action: str | None = None):
    query = select(AuditLog).where(AuditLog.company_id == company_id)
    if action:
        query = query.where(AuditLog.action == action)
    return session.exec(query.order_by(AuditLog.id)).all()


def refusal_codes(session, company_id: int) -> list[str]:
    return [
        row.result["code"] for row in audits(session, company_id, "rsi_cycle_refused")
    ]
