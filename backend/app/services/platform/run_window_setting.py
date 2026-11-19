"""A company's run window: read, previewed, its holiday calendars, and changed
by an Owner, audited (issue #736; the window itself is
``services/contracts/run_window.py``, the gate that applies it
``services/loop/run_window_gate.py``).

``companies.run_window`` is ``null`` (off: work starts at once, as before
migration 0055) or a window. Any member reads it with
``GET /v1/companies/{id}/run_window``; only an Owner changes it
(``PUT``), never a Manager and never the Manager agent, and each change is
audited as ``run_window_changed`` with both values and the account
(``user_id``, and ``token_id`` for an API token).

The named holiday calendars are data shipped with the backend, one JSON file
per year under ``holiday_calendars/<name>/<year>.json``, each citing the
notice it was copied from. A year without a file has no holidays in that
calendar: the window then treats those days as ordinary days, and an Owner
can add dates of their own to the window (``holidays.dates``).
"""

from __future__ import annotations

import json
from datetime import date, datetime
from functools import cache
from pathlib import Path
from typing import Any

from models.company import Company
from sqlmodel import Session

from services.contracts import run_window
from services.platform.agent_service import AgentService

AUDIT_ACTION = "run_window_changed"
CALENDARS_DIR = Path(__file__).resolve().parent / "holiday_calendars"


class RunWindowRefused(ValueError):
    """A change the backend refuses; ``detail`` is the HTTP body's."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


@cache
def _calendar_files() -> dict[str, list[dict[str, Any]]]:
    calendars: dict[str, list[dict[str, Any]]] = {}
    if not CALENDARS_DIR.is_dir():
        return calendars
    for folder in sorted(p for p in CALENDARS_DIR.iterdir() if p.is_dir()):
        years = []
        for path in sorted(folder.glob("*.json")):
            years.append(json.loads(path.read_text(encoding="utf-8")))
        calendars[folder.name] = years
    return calendars


def calendars() -> dict[str, dict[str, Any]]:
    """Each shipped calendar: its years and the source of each."""
    return {
        name: {
            "name": years[0].get("name") if years else name,
            "years": [
                {"year": y.get("year"), "source": y.get("source")} for y in years
            ],
        }
        for name, years in _calendar_files().items()
    }


def calendar_dates(name: str) -> frozenset[date]:
    """The holiday dates of a shipped calendar, every year it has."""
    return frozenset(
        date.fromisoformat(day)
        for year in _calendar_files().get(name, [])
        for holiday in year.get("holidays", [])
        for day in holiday.get("dates", [])
    )


def holidays_for(window: dict[str, Any]) -> frozenset[date]:
    """The dates on which *window* is open all day."""
    dates = set(run_window.own_dates(window))
    for name in run_window.calendars_of(window):
        dates |= calendar_dates(name)
    return frozenset(dates)


def stored(session: Session, company_id: int | None) -> dict[str, Any] | None:
    """The company's window, or ``None`` (off; also for an unknown company)."""
    company = session.get(Company, company_id) if company_id is not None else None
    return run_window.parse(company.run_window if company else None)


def status(window: dict[str, Any] | None, now: datetime) -> dict[str, Any]:
    """Whether *window* is open at *now*, when it next changes, and the next 7 days."""
    if window is None:
        return {"open": True, "next_open": None, "next_change": None, "preview": []}
    holidays = holidays_for(window)
    opens = run_window.next_open(window, now, holidays)
    change = run_window.next_change(window, now, holidays)
    return {
        "open": run_window.is_open(window, now, holidays),
        "next_open": run_window.local_iso(window, opens) if opens else None,
        "next_change": run_window.local_iso(window, change) if change else None,
        "preview": [
            {
                "from": run_window.local_iso(window, start),
                "to": run_window.local_iso(window, end),
            }
            for start, end in run_window.spans(window, now, holidays)
        ],
    }


def presets() -> dict[str, dict[str, Any]]:
    return {name: dict(preset) for name, preset in run_window.PRESETS.items()}


def state(session: Session, company_id: int, now: datetime) -> dict[str, Any]:
    """What ``GET /v1/companies/{id}/run_window`` shows."""
    window = stored(session, company_id)
    return {
        "company_id": company_id,
        "run_window": window,
        "now": now.isoformat(),
        **status(window, now),
        "presets": presets(),
        "calendars": calendars(),
    }


def preview(raw: Any, now: datetime) -> dict[str, Any]:
    """A window not stored yet, checked and previewed; ``RunWindowRefused`` (422) else."""
    try:
        window = run_window.validate(raw)
    except run_window.InvalidWindow as exc:
        raise RunWindowRefused(422, str(exc)) from exc
    return {"run_window": window, "now": now.isoformat(), **status(window, now)}


def set_window(
    session: Session,
    company_id: int,
    raw: Any,
    *,
    by: str,
    user_id: int | None,
    token_id: int | None,
    now: datetime,
) -> dict[str, Any]:
    """Set or clear the company's window (an Owner's call, checked by the router); audited."""
    company = session.get(Company, company_id)
    if company is None:
        raise RunWindowRefused(404, "Company not found")
    try:
        window = run_window.validate(raw)
    except run_window.InvalidWindow as exc:
        raise RunWindowRefused(422, str(exc)) from exc
    calendar = (window or {}).get("holidays", {}).get("calendar")
    if calendar and calendar not in _calendar_files():
        raise RunWindowRefused(
            422,
            f"unknown holiday calendar {calendar!r}; shipped: "
            f"{', '.join(_calendar_files()) or 'none'}",
        )
    before = run_window.parse(company.run_window)
    if before != window:
        company.run_window = window
        session.add(company)
        AgentService().audit_interaction(
            session=session,
            company_id=company_id,
            interaction_id=f"company:{company_id}:run_window",
            agent_id=f"human:{by}",
            action=AUDIT_ACTION,
            details={
                "company_id": str(company_id),
                "user_id": user_id,
                "token_id": token_id,
            },
            result={"from": before, "to": window},
            decision_signature=None,
            cost_tokens=0,
        )
        session.commit()
    return state(session, company_id, now)
