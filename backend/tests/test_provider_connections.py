"""A company's model provider connections (``routers/providers.py``,
``services/provider_connections.py``; issue #356, step 1): a Manager connects,
changes and removes them, a Viewer reads them, nobody outside the company
does; the key is encrypted at rest and never returned; an omitted key keeps
the stored one and an empty one clears it; the base URL is checked; the
validation lists the provider's models with the key and records the outcome,
which a later change to the key or URL clears."""

from __future__ import annotations

import httpx
import pytest
from factories import company as _company
from factories import enforcement  # noqa: F401 - the fixture, by name
from factories import member_headers as _member
from models import ProviderConnection
from services.models import provider_connections as connections

pytestmark = pytest.mark.usefixtures("enforcement")


def _url(company_id: int, suffix: str = "") -> str:
    return f"/v1/companies/{company_id}/providers{suffix}"


def test_a_manager_connects_a_provider_and_the_key_never_comes_back(
    sqlite_session, test_client
):
    company, other = _company(sqlite_session), _company(sqlite_session)
    manager = _member(sqlite_session, company.id, "Manager")
    viewer = _member(sqlite_session, company.id, "Viewer")
    outsider = _member(sqlite_session, other.id, "Manager")
    body = {
        "provider": "DeepSeek",
        "api_key": "sk-secret-123",
        "headers": {"X-Org": "acme"},
    }
    assert (
        test_client.post(_url(company.id), json=body, headers=viewer).status_code == 403
    )
    assert (
        test_client.post(_url(company.id), json=body, headers=outsider).status_code
        == 403
    )
    created = test_client.post(_url(company.id), json=body, headers=manager)
    assert created.status_code == 201, created.text
    data = created.json()
    assert data["provider"] == "deepseek" and data["name"] == "deepseek"
    assert data["base_url"] == "https://api.deepseek.com/v1"
    assert data["api_key_set"] is True and data["header_names"] == ["X-Org"]
    assert "sk-secret-123" not in created.text and "acme" not in created.text
    # Encrypted at rest (or at least prefixed, without a Fernet key).
    row = sqlite_session.get(ProviderConnection, data["id"])
    assert (
        row.api_key != "sk-secret-123"
        and connections.read_secrets(row)[0] == "sk-secret-123"
    )
    listed = test_client.get(_url(company.id), headers=viewer)
    assert listed.status_code == 200 and "sk-secret-123" not in listed.text
    assert [p["name"] for p in listed.json()["providers"]] == ["deepseek"]
    assert "deepseek" in listed.json()["known_providers"]
    assert test_client.get(_url(company.id), headers=outsider).status_code == 403
    # The same name twice is a conflict.
    again = test_client.post(_url(company.id), json=body, headers=manager)
    assert again.status_code == 409


def test_base_urls_and_headers_are_checked(sqlite_session, test_client):
    company = _company(sqlite_session)
    manager = _member(sqlite_session, company.id, "Manager")
    cases = [
        ({"provider": "openai-compatible"}, "base_url is required"),
        ({"provider": "bad id!"}, "models.dev id"),
        ({"provider": "openai-compatible", "base_url": "ftp://x"}, "http(s)"),
        (
            {"provider": "openai-compatible", "base_url": "https://u:p@h/v1"},
            "credentials",
        ),
        ({"provider": "openai-compatible", "base_url": "https://h/v1?k=1"}, "query"),
        (
            {"provider": "deepseek", "headers": {"Authorization": "x"}},
            "set by La Fabrique",
        ),
        ({"provider": "deepseek", "headers": {"X-A": "a\nb"}}, "line break"),
    ]
    for body, message in cases:
        response = test_client.post(_url(company.id), json=body, headers=manager)
        assert response.status_code == 422 and message in response.text, body
    # A local server is allowed on purpose (Ollama, LM Studio), with no key.
    local = test_client.post(
        _url(company.id),
        json={
            "provider": "openai-compatible",
            "name": "Ollama",
            "base_url": "http://localhost:11434/v1/",
        },
        headers=manager,
    )
    assert local.status_code == 201, local.text
    assert local.json()["base_url"] == "http://localhost:11434/v1"
    assert local.json()["api_key_set"] is False


def test_an_omitted_key_is_kept_an_empty_one_cleared_and_a_change_resets_the_check(
    sqlite_session, test_client
):
    company, other = _company(sqlite_session), _company(sqlite_session)
    manager = _member(sqlite_session, company.id, "Manager")
    viewer = _member(sqlite_session, company.id, "Viewer")
    created = test_client.post(
        _url(company.id),
        json={"provider": "openrouter", "api_key": "k1"},
        headers=manager,
    ).json()
    pid = created["id"]
    row = sqlite_session.get(ProviderConnection, pid)
    row.validation_ok, row.validation_detail = True, "3 models listed"
    sqlite_session.add(row)
    sqlite_session.commit()
    renamed = test_client.patch(
        _url(company.id, f"/{pid}"),
        json={"name": "Router", "enabled": False},
        headers=manager,
    ).json()
    assert renamed["name"] == "Router" and renamed["enabled"] is False
    assert renamed["api_key_set"] is True and renamed["validation_ok"] is True
    cleared = test_client.patch(
        _url(company.id, f"/{pid}"), json={"api_key": ""}, headers=manager
    ).json()
    assert cleared["api_key_set"] is False and cleared["validation_ok"] is None
    assert (
        test_client.patch(
            _url(company.id, f"/{pid}"), json={"name": "x"}, headers=viewer
        ).status_code
        == 403
    )
    # Another company's id is not found through this company.
    assert (
        test_client.patch(
            _url(other.id, f"/{pid}"),
            json={"name": "x"},
            headers=_member(sqlite_session, other.id, "Manager"),
        ).status_code
        == 404
    )
    assert (
        test_client.delete(_url(company.id, f"/{pid}"), headers=viewer).status_code
        == 403
    )
    assert (
        test_client.delete(_url(company.id, f"/{pid}"), headers=manager).status_code
        == 204
    )
    assert test_client.get(_url(company.id), headers=viewer).json()["providers"] == []


def _row(**kw) -> ProviderConnection:
    row = ProviderConnection(
        company_id=1, provider="deepseek", name="d", base_url="https://api.example/v1"
    )
    connections.set_secrets(row, kw.get("api_key", "sk-1"), kw.get("headers"))
    return row


def test_the_validation_lists_models_with_the_key_and_records_the_outcome():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["org"] = request.headers.get("x-org")
        return httpx.Response(
            200, json={"data": [{"id": "deepseek-v4-pro"}, {"id": "deepseek-flash"}]}
        )

    row = _row(headers={"X-Org": "acme"})
    result = connections.validate(row, transport=httpx.MockTransport(handler))
    assert seen == {
        "url": "https://api.example/v1/models",
        "auth": "Bearer sk-1",
        "org": "acme",
    }
    assert result["ok"] is True and result["models"] == [
        "deepseek-flash",
        "deepseek-v4-pro",
    ]
    assert row.validation_ok is True and row.validated_at is not None
    assert row.validation_detail == "2 models listed"


@pytest.mark.parametrize(
    ("response", "detail"),
    [
        (httpx.Response(401, json={}), "refused the key (401)"),
        (httpx.Response(500, text="boom"), "answered 500"),
        (httpx.Response(200, text="not json"), "not a model list"),
    ],
)
def test_a_failed_validation_says_why(response, detail):
    row = _row()
    result = connections.validate(
        row, transport=httpx.MockTransport(lambda r: response)
    )
    assert result["ok"] is False and detail in result["detail"]
    assert row.validation_ok is False and "sk-1" not in result["detail"]


def test_an_unreachable_provider_and_a_keyless_one():
    def down(request):
        raise httpx.ConnectError("refused", request=request)

    row = _row()
    result = connections.validate(row, transport=httpx.MockTransport(down))
    assert result["ok"] is False and "could not reach" in result["detail"]
    keyless = _row(api_key="")
    seen = {}

    def ok(request):
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"data": []})

    assert (
        connections.validate(keyless, transport=httpx.MockTransport(ok))["ok"] is True
    )
    assert seen["auth"] is None


def test_the_validate_route_records_and_needs_a_manager(
    sqlite_session, test_client, monkeypatch
):
    company = _company(sqlite_session)
    manager = _member(sqlite_session, company.id, "Manager")
    viewer = _member(sqlite_session, company.id, "Viewer")
    pid = test_client.post(
        _url(company.id), json={"provider": "deepseek", "api_key": "k"}, headers=manager
    ).json()["id"]
    real = connections.validate
    monkeypatch.setattr(
        connections,
        "validate",
        lambda row: real(
            row,
            transport=httpx.MockTransport(
                lambda r: httpx.Response(200, json={"data": [{"id": "m"}]})
            ),
        ),
    )
    assert (
        test_client.post(
            _url(company.id, f"/{pid}/validate"), headers=viewer
        ).status_code
        == 403
    )
    checked = test_client.post(_url(company.id, f"/{pid}/validate"), headers=manager)
    assert checked.status_code == 200, checked.text
    assert checked.json()["ok"] is True and checked.json()["models"] == ["m"]
    assert checked.json()["provider"]["validation_ok"] is True
