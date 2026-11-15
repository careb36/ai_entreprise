"""Saved views of a company's tasks: who sees them, who changes them (issue #535).

A view is company-scoped (``models/saved_view.py``). Its ``access`` decides
who lists and reads it: ``company`` for every member, ``private`` for its
author alone (any other member, a Manager included, is answered as if it did
not exist). Who changes it:

- a private view: its author;
- a company view: its author or a Manager (the Owner included);
- a locked view: only a Manager, so a Viewer's change is refused with 403
  even on a view they wrote;
- making a view ``company`` or ``locked`` takes a Manager.

The settings are checked by ``services/contracts/task_filters.parse_settings``
before anything is stored. A stored filter is parsed again whenever the view
is read, so a view naming a field no longer in the allowlist is reported
``valid: false`` and never evaluated in part. A person's display preferences
are kept apart (``SavedViewPreference``), one row per view and person, so
they never change the view the company shares.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from models import SavedView, SavedViewPreference
from sqlmodel import Session, or_, select

from services.contracts import task_filters

DENSITIES = ("comfortable", "compact")
MAX_DISPLAY_BYTES = 4096


class ViewError(Exception):
    """A refused read or change of a view; ``status`` is the HTTP status."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def visible(view: SavedView, user_id: int | None) -> bool:
    """Whether the person *user_id* (``None`` without sign-in) lists *view*."""
    return view.access == "company" or view.owner_user_id == user_id


def views_of(session: Session, company_id: int, user_id: int | None) -> list[SavedView]:
    """The company's views this person lists: every company view and their own."""
    mine = (
        SavedView.owner_user_id == user_id
        if user_id is not None
        else SavedView.owner_user_id.is_(None)  # type: ignore[union-attr]
    )
    return list(
        session.exec(
            select(SavedView)
            .where(SavedView.company_id == company_id)
            .where(or_(SavedView.access == "company", mine))
            .order_by(SavedView.name, SavedView.id)  # type: ignore[arg-type]
        ).all()
    )


def readable(session: Session, view_id: int, user_id: int | None) -> SavedView:
    """The view, or ``ViewError(404)`` when it is missing or another's private."""
    view = session.get(SavedView, view_id)
    if view is None or not visible(view, user_id):
        raise ViewError(404, "View not found")
    return view


def _check_rights(
    settings: dict[str, Any], is_manager: bool, view: SavedView | None
) -> None:
    if (
        not is_manager
        and settings["access"] == "company"
        and (view is None or view.access != "company")
    ):
        raise ViewError(403, "Sharing a view with the company takes a Manager")
    if not is_manager and settings["locked"] != (view.locked if view else False):
        raise ViewError(403, "Locking or unlocking a view takes a Manager")


def create(
    session: Session,
    company_id: int,
    raw: dict[str, Any],
    *,
    user_id: int | None,
    actor: str,
    is_manager: bool,
) -> SavedView:
    """A new view from *raw*; raises ``FilterRefused`` or ``ViewError``. Commits."""
    settings = task_filters.parse_settings(raw)
    _check_rights(settings, is_manager, None)
    view = SavedView(
        company_id=company_id, owner_user_id=user_id, owner_name=actor, **settings
    )
    session.add(view)
    session.commit()
    session.refresh(view)
    return view


def may_change(view: SavedView, user_id: int | None, is_manager: bool) -> bool:
    if view.locked:
        return is_manager
    if view.access == "private":
        return view.owner_user_id == user_id
    return is_manager or view.owner_user_id == user_id


def update(
    session: Session,
    view: SavedView,
    raw: dict[str, Any],
    *,
    user_id: int | None,
    actor: str,
    is_manager: bool,
) -> SavedView:
    """*view* with the settings of *raw* (merged over its own). Commits."""
    if not may_change(view, user_id, is_manager):
        raise ViewError(
            403, "The view is locked" if view.locked else "Only its author changes it"
        )
    merged = {**as_settings(view), **raw}
    settings = task_filters.parse_settings(merged)
    _check_rights(settings, is_manager, view)
    if settings["access"] == "private" and view.owner_user_id != user_id:
        raise ViewError(403, "Only its author makes a view private")
    for key, value in settings.items():
        setattr(view, key, value)
    view.updated_by = actor
    view.updated_at = datetime.now(UTC)
    session.add(view)
    session.commit()
    session.refresh(view)
    return view


def delete(
    session: Session, view: SavedView, *, user_id: int | None, is_manager: bool
) -> None:
    """Remove *view* and every person's preferences for it. Commits."""
    if not may_change(view, user_id, is_manager):
        raise ViewError(
            403, "The view is locked" if view.locked else "Only its author changes it"
        )
    for pref in session.exec(
        select(SavedViewPreference).where(SavedViewPreference.view_id == view.id)
    ).all():
        session.delete(pref)
    session.delete(view)
    session.commit()


def as_settings(view: SavedView) -> dict[str, Any]:
    return {
        "name": view.name,
        "filters": view.filters or {},
        "group_by": view.group_by,
        "order_by": view.order_by,
        "layout": view.layout,
        "visible_columns": list(view.visible_columns or []),
        "access": view.access,
        "locked": view.locked,
    }


def stored_filter(view: SavedView) -> dict[str, Any] | None:
    """The view's filter parsed again; raises ``FilterRefused`` when it no
    longer parses (a field taken off the allowlist)."""
    return task_filters.parse_filter(view.filters or None)


def as_dict(view: SavedView, user_id: int | None, is_manager: bool) -> dict[str, Any]:
    try:
        stored_filter(view)
        valid, reason = True, None
    except task_filters.FilterRefused as exc:
        valid, reason = False, exc.as_detail()
    return {
        "id": view.id,
        "company_id": view.company_id,
        **as_settings(view),
        "owner_name": view.owner_name,
        "mine": view.owner_user_id == user_id,
        "can_edit": may_change(view, user_id, is_manager),
        "valid": valid,
        "invalid_reason": reason,
        "updated_by": view.updated_by,
        "created_at": view.created_at.isoformat() if view.created_at else None,
        "updated_at": view.updated_at.isoformat() if view.updated_at else None,
    }


# --- One person's display preferences ----------------------------------------


def check_display(raw: Any) -> dict[str, Any]:
    """The display preferences, checked: collapsed groups, column widths, density."""
    if not isinstance(raw, dict):
        raise ViewError(422, "The preferences are an object")
    extra = set(raw) - {"collapsed_groups", "column_widths", "density"}
    if extra:
        raise ViewError(422, f"Unknown preferences: {sorted(extra)}")
    groups = raw.get("collapsed_groups", [])
    if not isinstance(groups, list) or not all(
        isinstance(g, str) and len(g) <= task_filters.MAX_TEXT for g in groups
    ):
        raise ViewError(422, "collapsed_groups is a list of group names")
    widths = raw.get("column_widths", {})
    if not isinstance(widths, dict) or not all(
        c in task_filters.COLUMNS
        and isinstance(w, int)
        and not isinstance(w, bool)
        and 40 <= w <= 800
        for c, w in widths.items()
    ):
        raise ViewError(422, "column_widths maps a column to 40 to 800 pixels")
    density = raw.get("density", "comfortable")
    if density not in DENSITIES:
        raise ViewError(422, f"density is one of {DENSITIES}")
    display = {
        "collapsed_groups": list(dict.fromkeys(groups)),
        "column_widths": widths,
        "density": density,
    }
    if len(json.dumps(display)) > MAX_DISPLAY_BYTES:
        raise ViewError(422, f"The preferences are over {MAX_DISPLAY_BYTES} bytes")
    return display


def _preference(
    session: Session, view: SavedView, user_id: int | None
) -> SavedViewPreference | None:
    who = (
        SavedViewPreference.user_id == user_id
        if user_id is not None
        else SavedViewPreference.user_id.is_(None)  # type: ignore[union-attr]
    )
    return session.exec(
        select(SavedViewPreference)
        .where(SavedViewPreference.view_id == view.id)
        .where(who)
    ).first()


def preferences(session: Session, view: SavedView, user_id: int | None) -> dict:
    pref = _preference(session, view, user_id)
    return check_display(pref.display if pref else {})


def set_preferences(
    session: Session, view: SavedView, user_id: int | None, raw: Any
) -> dict[str, Any]:
    """Store this person's preferences for *view*; any reader of a view may,
    a locked one included, since nothing of the view changes. Commits."""
    display = check_display(raw)
    pref = _preference(session, view, user_id)
    if pref is None:
        pref = SavedViewPreference(
            company_id=view.company_id, view_id=view.id, user_id=user_id
        )
    pref.display = display
    pref.updated_at = datetime.now(UTC)
    session.add(pref)
    session.commit()
    return display
