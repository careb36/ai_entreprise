"""Every route declares who may call it, and the declaration holds
(issue #541; WSTG-v42-ATHZ-02 and ATHZ-04, WSTG-APIT-02 and APIT-04 at
latest@87cf4a4).

The declaration is data, ``docs/architecture/authorization.json``: for each
operation of ``app.openapi()``, its ``access``:

- ``public``: anyone (login, registration, health, the model catalogue...);
- ``signed``: public, but the request must carry a valid signature or code
  (the GitHub webhook, Slack and Teams, the SSO callback); unsigned, refused;
- ``authenticated``: any signed-in caller, the data limited to their own
  companies or tickets;
- ``support_admin``: a help-desk administrator (``SUPPORT_ADMIN_EMAILS``);
- ``Viewer``, ``Manager``, ``Owner``: the minimum role in the company the
  request names (by path, query, body or the resource it points at).

An operation missing from the file fails (deny by default): a new route
cannot ship without someone deciding who may call it. Then each route is
called, with ``AUTH_REQUIRED`` on, as an anonymous caller (401 unless public),
and a company-scoped one as a Viewer, a Manager and an Owner of company A
and as the Owner of company B: a role below the minimum gets 403, a member
of another company 403 or 404, and the minimum role gets past the check
(anything but 401 and 403). ``tests/authz_world.py`` builds the data.

With ``AUTHZ_DISCOVER=<file>`` the test records what each caller got instead
of asserting, to draft a declaration for review; it is never the source of
truth.
"""

from __future__ import annotations

import json
import os
import secrets
from pathlib import Path

import auth_tokens
import pytest
from authz_world import ROLES, build_world, request_for
from config import settings

from app.main import app

DECLARATION = (
    Path(__file__).resolve().parents[2] / "docs" / "architecture" / "authorization.json"
)
ACCESS = {"public", "signed", "authenticated", "support_admin", *ROLES}
DISCOVER = os.environ.get("AUTHZ_DISCOVER")
_OPENAPI = app.openapi()
_DECLARED = json.loads(DECLARATION.read_text())["routes"]


def _operations() -> dict[str, tuple[str, str, dict]]:
    return {
        f"{method.upper()} {path}": (method, path, operation)
        for path, operations in _OPENAPI["paths"].items()
        for method, operation in operations.items()
    }


OPERATIONS = _operations()


def test_every_route_declares_its_access():
    undeclared = sorted(set(OPERATIONS) - set(_DECLARED))
    stale = sorted(set(_DECLARED) - set(OPERATIONS))
    assert not undeclared, f"routes with no declared access: {undeclared}"
    assert not stale, f"declared routes that no longer exist: {stale}"
    wrong = {
        k: v.get("access")
        for k, v in _DECLARED.items()
        if v.get("access") not in ACCESS
    }
    assert not wrong, f"unknown access levels: {wrong}"


def test_no_route_hides_from_the_schema():
    """The matrix walks ``app.openapi()``; a route left out of it would
    escape the declaration."""
    app_dir = Path(__file__).resolve().parents[1] / "app"
    hidden = [
        str(path.relative_to(app_dir))
        for path in app_dir.rglob("*.py")
        if "include_in_schema=False" in path.read_text(encoding="utf-8")
    ]
    assert hidden == []


def test_the_public_routes_are_the_documented_ones():
    """SECURITY.md §3.2 lists the public routes; a new one is a decision."""
    public = sorted(
        k for k, v in _DECLARED.items() if v["access"] in {"public", "signed"}
    )
    assert public == sorted(json.loads(DECLARATION.read_text())["public_routes"])


@pytest.fixture
def world(sqlite_session, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "AUTH_REQUIRED", True)
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path))
    key = secrets.token_urlsafe(48)
    monkeypatch.setattr(auth_tokens, "_configured_key", lambda: key)
    built = build_world(sqlite_session)
    admin = f"authz-admin-{secrets.token_hex(4)}@example.com"
    from models import User

    user = User(email=admin, full_name="Authz admin", password_hash="unused")
    sqlite_session.add(user)
    sqlite_session.commit()
    monkeypatch.setattr(settings, "SUPPORT_ADMIN_EMAILS", admin)
    built.headers["support_admin"] = {
        "Authorization": "Bearer "
        + auth_tokens.create_access_token(user_id=user.id, email=admin)
    }
    return built


def _send(client, call, headers):
    return client.request(
        call.method, call.path, params=call.params, json=call.json, headers=headers
    )


def _record(key, observed):
    path = Path(DISCOVER)
    data = json.loads(path.read_text()) if path.exists() else {}
    data[key] = observed
    path.write_text(json.dumps(data, indent=1, sort_keys=True))


CALLERS = ("anonymous", "Viewer", "Manager", "Owner", "outsider", "support_admin")


@pytest.mark.parametrize("key", sorted(OPERATIONS))
def test_the_declared_access_holds(key, test_client, world):
    method, path, operation = OPERATIONS[key]
    declared = _DECLARED.get(key, {"access": "undeclared"})
    access = declared["access"]
    call = request_for(
        method, path, operation, _OPENAPI["components"]["schemas"], world, declared
    )

    def status(caller):
        headers = {} if caller == "anonymous" else world.headers[caller]
        return _send(test_client, call, headers).status_code

    if DISCOVER:
        # Callers that are refused first, so an allowed call that changes or
        # deletes the fixture does not hide a refusal.
        order = ["anonymous", "outsider", "Viewer", "Manager", "support_admin", "Owner"]
        _record(key, {caller: status(caller) for caller in order})
        return

    allowed = set(declared.get("allowed_status", []))

    def passes(code):
        return code not in (401, 403) or code in allowed

    if access == "public":
        assert status("anonymous") != 401 or 401 in allowed
        return
    if access == "signed":
        assert status("anonymous") in {400, 401, 403, 404}, "unsigned request accepted"
        return
    assert status("anonymous") == 401, "anonymous caller not refused"
    if access == "authenticated":
        assert passes(status("outsider"))
        return
    if access == "support_admin":
        assert status("Owner") == 403
        assert passes(status("support_admin"))
        return
    minimum = ROLES.index(access)
    other = status("outsider")
    assert other in {403, 404}, f"a member of another company got {other}"
    for role in ROLES[:minimum]:
        code = status(role)
        assert code == 403, f"{role} below {access} got {code}"
    code = status(access)
    assert passes(code), f"{access} refused with {code}"
