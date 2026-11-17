"""No error answer of the API carries a traceback, a file path or SQL text
(issue #541; WSTG-v42-ERRH-01 and ERRH-02).

Every operation of ``app.openapi()`` is called by the Owner of a company
with: malformed JSON, a body of the wrong type, a 2 MiB body, and path
parameters that overflow a database integer. Whatever it answers, a 4xx or
5xx body must contain none of the markers below. An unhandled exception
answers ``{"detail": "Internal server error", "error_id": ...}`` and nothing
else; its report (NATS ticket, optional ``ERROR_REPORT_URL``) is redacted to
the error id, the exception's class and the route template.
"""

from __future__ import annotations

import asyncio
import json
import re
import secrets

import auth_tokens
import exception_handlers
import httpx
import pytest
from authz_world import build_world, request_for
from config import settings
from conftest import _FakeNATSClient
from services.integrations import webhook_client

from app.main import app

_OPENAPI = app.openapi()
_COMPONENTS = _OPENAPI["components"]["schemas"]
OPERATIONS = sorted(
    (f"{method.upper()} {path}", method, path, operation)
    for path, operations in _OPENAPI["paths"].items()
    for method, operation in operations.items()
)
MARKERS = re.compile(
    r"Traceback|File \"|\.py\b|/home/|/usr/|/app/|site-packages|sqlalchemy|"
    r"psycopg|sqlite3|OperationalError|IntegrityError|ProgrammingError|"
    r"\bSELECT\s|\bINSERT INTO\b|\bUPDATE\s+\w+\s+SET\b|\bDELETE FROM\b",
    re.IGNORECASE,
)
HUGE = "9" * 30


@pytest.fixture
def owner(sqlite_session, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "AUTH_REQUIRED", True)
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path))
    key = secrets.token_urlsafe(48)
    monkeypatch.setattr(auth_tokens, "_configured_key", lambda: key)
    return build_world(sqlite_session)


def _clean(response, case):
    if response.status_code < 400:
        return
    found = MARKERS.search(response.text)
    assert found is None, f"{case}: {response.status_code} leaks {found.group(0)!r}"
    if response.status_code == 500:
        body = response.json()
        assert set(body) <= {"detail", "error_id"}, f"{case}: {body}"


@pytest.mark.parametrize(
    "key, method, path, operation", OPERATIONS, ids=[o[0] for o in OPERATIONS]
)
def test_no_error_body_leaks_internals(
    test_client, owner, key, method, path, operation
):
    call = request_for(method, path, operation, _COMPONENTS, owner, {})
    headers = {**owner.headers["Owner"]}
    send = lambda **kw: test_client.request(call.method, kw.pop("url", call.path), **kw)
    json_body = "application/json" in (operation.get("requestBody") or {}).get(
        "content", {}
    )

    if json_body:
        broken = send(
            params=call.params,
            content=b'{"broken": ',
            headers={**headers, "Content-Type": "application/json"},
        )
        _clean(broken, "malformed JSON")
        assert broken.status_code in {400, 422, 401, 403, 404}, broken.text
        wrong = send(params=call.params, json=[{"x": [[]]}], headers=headers)
        _clean(wrong, "wrong type")
        typed = (
            {k: {"nested": [[]]} for k in call.json}
            if isinstance(call.json, dict)
            else {"x": {"nested": [[]]}}
        )
        _clean(
            send(params=call.params, json=typed, headers=headers), "wrong field types"
        )
        big = send(
            params=call.params,
            content=json.dumps("a" * (2 * 1024 * 1024)).encode(),
            headers={**headers, "Content-Type": "application/json"},
        )
        _clean(big, "oversized body")
    params = {k: (HUGE if isinstance(v, int) else v) for k, v in call.params.items()}
    url = re.sub(r"/\d+(?=/|$)", "/" + HUGE, call.path)
    _clean(
        send(url=url, params=params, json=call.json, headers=headers), "overflowing ids"
    )
    _clean(
        send(
            params={**call.params, "limit": "-1", "since": "not-a-date"},
            json=call.json,
            headers=headers,
        ),
        "wrong query types",
    )


# -- the handler of unhandled errors --------------------------------------


class _Nats(_FakeNATSClient):
    def __init__(self):
        self.published = []

    async def publish(self, subject, payload, msg_id=None):
        self.published.append((subject, json.loads(payload)))


@pytest.fixture
def boom(monkeypatch):
    from app import main

    def explode(request):
        raise RuntimeError("/srv/app/secret.py: SELECT password FROM users")

    monkeypatch.setattr(main, "_nats_health", explode)


def test_an_unhandled_error_answers_an_id_only(test_client, boom):
    response = test_client.get("/health")
    assert response.status_code == 500
    body = response.json()
    assert body["detail"] == "Internal server error"
    assert re.fullmatch(r"[0-9a-f]{16}", body["error_id"])
    assert "secret" not in response.text and "Vendor" not in response.text


def test_the_ticket_is_published_and_redacted(test_client, boom, monkeypatch):
    nats = _Nats()
    monkeypatch.setattr(app.state, "nats_client", nats)
    response = test_client.get("/health")
    [(subject, ticket)] = nats.published
    assert subject == "system.maintenance.create_ticket"
    assert ticket["error_id"] == response.json()["error_id"]
    assert ticket["error_type"] == "RuntimeError"
    assert ticket["route"] == "/health" and ticket["method"] == "GET"
    assert "secret" not in json.dumps(ticket) and "SELECT" not in json.dumps(ticket)


def test_the_route_template_is_reported_not_the_path(test_client, owner, monkeypatch):
    """A concrete path can carry identifiers; the report names the template."""
    nats = _Nats()
    monkeypatch.setattr(app.state, "nats_client", nats)
    from routers import sprint_crud

    def explode(*args, **kwargs):
        raise ValueError("boom")

    monkeypatch.setattr(sprint_crud, "require_company_role", explode, raising=False)
    sprint = owner.ids["sprint_id"]
    response = test_client.patch(
        f"/v1/sprints/{sprint}", json={"goal": "x"}, headers=owner.headers["Owner"]
    )
    assert response.status_code == 500
    [(_, ticket)] = nats.published
    assert ticket["route"] == "/v1/sprints/{sprint_id}"


def test_no_report_leaves_without_an_https_url(test_client, boom, monkeypatch):
    posted = []
    transport = httpx.MockTransport(lambda r: posted.append(r) or httpx.Response(204))
    monkeypatch.setattr(webhook_client, "TRANSPORT", transport)
    monkeypatch.setattr(settings, "ERROR_REPORT_URL", "")
    test_client.get("/health")
    monkeypatch.setattr(settings, "ERROR_REPORT_URL", "http://errors.example.com/in")
    test_client.get("/health")
    assert posted == []


def test_the_opt_in_report_is_redacted(test_client, boom, monkeypatch):
    posted = []
    transport = httpx.MockTransport(lambda r: posted.append(r) or httpx.Response(204))
    monkeypatch.setattr(webhook_client, "TRANSPORT", transport)
    monkeypatch.setattr(settings, "ERROR_REPORT_URL", "https://errors.example.com/in")
    monkeypatch.setattr(settings, "ERROR_REPORT_SOURCE", "staging")
    response = test_client.get("/health")
    [request] = posted
    report = json.loads(request.content)
    assert report == {
        "error_id": response.json()["error_id"],
        "error_type": "RuntimeError",
        "method": "GET",
        "route": "/health",
        "source": "staging",
    }


def test_a_failed_report_does_not_change_the_answer(test_client, boom, monkeypatch):
    def refuse(request):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(webhook_client, "TRANSPORT", httpx.MockTransport(refuse))
    monkeypatch.setattr(settings, "ERROR_REPORT_URL", "https://errors.example.com/in")
    assert test_client.get("/health").status_code == 500


def test_the_report_builder_carries_no_request_data():
    class _Request:
        method = "POST"

        def __init__(self):
            route = type("R", (), {"path_format": "/v1/things/{thing_id}"})()
            self.scope = {"route": route}

    report = exception_handlers.error_report(
        _Request(), KeyError("/etc/passwd"), "0123456789abcdef"
    )
    assert report["route"] == "/v1/things/{thing_id}"
    assert "passwd" not in json.dumps(report)
    asyncio.run(exception_handlers._post_report(report))  # no URL: a no-op
