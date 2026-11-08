"""A company's model provider connections (``models/provider.py``).

Where a connection's requests go, how its secrets are stored, what the API
shows of it, and the check that its key works. The key and the headers are
encrypted at rest (``crypto.py``) and never serialized: the API says whether
a key is set, and which header names are configured.

The base URL is the provider's own when the catalogue knows it
(``model_catalog.base_url_for``, from models.dev) and whatever the Manager
gives for ``openai-compatible``. Local addresses are
allowed on purpose: Ollama, LM Studio and vLLM usually run on the same
computer or network.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import httpx
from crypto import decrypt_value, encrypt_value
from models.provider import ProviderConnection

from services import model_catalog

OPENAI_COMPATIBLE = "openai-compatible"


VALIDATION_TIMEOUT_SECONDS = 15.0
_PROVIDER_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_HEADER_NAME = re.compile(r"^[A-Za-z0-9-]{1,64}$")
# Headers the backend sets itself; a connection may not override them.
_RESERVED_HEADERS = {"authorization", "host", "content-length", "content-type"}


class ProviderConfigError(ValueError):
    """A connection that cannot be stored as given; the message says why."""


def normalize_provider(provider: str) -> str:
    value = (provider or "").strip().lower()
    if not _PROVIDER_ID.match(value):
        raise ProviderConfigError(
            "provider must be a models.dev id (e.g. deepseek, openrouter) "
            f"or {OPENAI_COMPATIBLE!r}"
        )
    return value


def normalize_base_url(provider: str, base_url: str | None) -> str:
    """The URL requests go to: the given one, else the known provider's."""
    url = (base_url or "").strip().rstrip("/")
    if not url:
        known = model_catalog.base_url_for(provider)
        if not known:
            raise ProviderConfigError(f"base_url is required for provider {provider!r}")
        return known
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ProviderConfigError("base_url must be an http(s) URL with a host")
    if parsed.username or parsed.password:
        raise ProviderConfigError(
            "base_url must not carry credentials; use api_key or headers"
        )
    if parsed.query or parsed.fragment:
        raise ProviderConfigError("base_url must not carry a query or fragment")
    return url


def normalize_headers(headers: dict[str, Any] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for name, value in (headers or {}).items():
        key = str(name).strip()
        if not _HEADER_NAME.match(key):
            raise ProviderConfigError(f"{name!r} is not a header name")
        if key.lower() in _RESERVED_HEADERS:
            raise ProviderConfigError(f"header {key!r} is set by La Fabrique")
        text = str(value)
        if "\n" in text or "\r" in text:
            raise ProviderConfigError(f"header {key!r} has a line break")
        out[key] = text
    return out


def set_secrets(
    row: ProviderConnection,
    api_key: str | None = None,
    headers: dict[str, Any] | None = None,
) -> None:
    """Store a key and headers on *row*. ``None`` keeps what is there; an
    empty key or an empty map clears it."""
    if api_key is not None:
        key = api_key.strip()
        row.api_key = encrypt_value(key) if key else None
    if headers is not None:
        clean = normalize_headers(headers)
        row.headers = encrypt_value(json.dumps(clean)) if clean else None


def read_secrets(row: ProviderConnection) -> tuple[str | None, dict[str, str]]:
    """The key and headers in clear, for a request to the provider."""
    key = decrypt_value(row.api_key) if row.api_key else None
    headers = json.loads(decrypt_value(row.headers)) if row.headers else {}
    return key, headers


def serialize(row: ProviderConnection) -> dict[str, Any]:
    """What the API shows: never the key or the header values."""
    try:
        header_names = sorted(read_secrets(row)[1])
        readable = True
    except Exception:  # noqa: BLE001 - A key this deployment cannot read.
        header_names, readable = [], False
    return {
        "id": row.id,
        "company_id": row.company_id,
        "provider": row.provider,
        "name": row.name,
        "base_url": row.base_url,
        "api_key_set": bool(row.api_key),
        "header_names": header_names,
        "secrets_readable": readable,
        "enabled": row.enabled,
        "validated_at": row.validated_at.isoformat() if row.validated_at else None,
        "validation_ok": row.validation_ok,
        "validation_detail": row.validation_detail,
        "listed_models": list(row.listed_models or []),
        "created_by": row.created_by,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def validate(
    row: ProviderConnection, transport: httpx.BaseTransport | None = None
) -> dict[str, Any]:
    """Ask the provider for its models with this connection's key.

    Lists models rather than sending a completion, so the check spends no
    tokens. Records the outcome on *row* (the caller commits) and returns
    it with the model ids the provider listed."""
    try:
        key, headers = read_secrets(row)
    except Exception as exc:  # noqa: BLE001 - Reported, not raised.
        return _record(
            row, False, f"the stored secrets cannot be read ({type(exc).__name__})"
        )
    request_headers = dict(headers)
    if key:
        request_headers["Authorization"] = f"Bearer {key}"
    try:
        with httpx.Client(
            timeout=VALIDATION_TIMEOUT_SECONDS, transport=transport
        ) as client:
            response = client.get(f"{row.base_url}/models", headers=request_headers)
    except httpx.HTTPError as exc:
        return _record(
            row, False, f"could not reach {row.base_url}: {type(exc).__name__}"
        )
    if response.status_code in (401, 403):
        return _record(
            row, False, f"the provider refused the key ({response.status_code})"
        )
    if response.status_code >= 400:
        return _record(row, False, f"the provider answered {response.status_code}")
    try:
        data = response.json().get("data") or []
        models = sorted(
            {str(m["id"]) for m in data if isinstance(m, dict) and m.get("id")}
        )
    except (ValueError, AttributeError, TypeError):
        return _record(row, False, "the provider's /models answer is not a model list")
    result = _record(row, True, f"{len(models)} models listed")
    row.listed_models = models
    result["models"] = models
    return result


def _record(row: ProviderConnection, ok: bool, detail: str) -> dict[str, Any]:
    row.validated_at = datetime.now(UTC).replace(tzinfo=None)
    row.validation_ok = ok
    row.validation_detail = detail[:500]
    return {"ok": ok, "detail": row.validation_detail, "models": []}


# A model of a company's connection, as an agent stores it: ``conn:<id>:<model>``.
CONNECTION_PREFIX = "conn:"


def model_ref(connection_id: int, model: str) -> str:
    return f"{CONNECTION_PREFIX}{connection_id}:{model}"


def parse_model_ref(model: str) -> tuple[int, str] | None:
    """``(connection_id, model)`` for a connection's model, else ``None``."""
    if not (model or "").startswith(CONNECTION_PREFIX):
        return None
    rest = model[len(CONNECTION_PREFIX) :]
    conn_id, _, name = rest.partition(":")
    if not conn_id.isdigit() or not name:
        raise ProviderConfigError(f"{model!r} is not conn:<id>:<model>")
    return int(conn_id), name


def proxy_route(
    model: str, company_id: int | None, engine: Any = None
) -> tuple[str, dict[str, str]]:
    """The model to send the proxy and the headers that route it.

    A plain model id passes through with no header. ``conn:<id>:<model>``
    loads the connection, checks it belongs to *company_id* and is enabled,
    and returns the headers that make the proxy call it with the company's
    key (``llm-proxy/connections.py``). Raises ``ProviderConfigError`` when
    that cannot be done, so the call fails with the reason instead of falling
    back to another provider."""
    import base64

    from config import settings

    ref = parse_model_ref(model)
    if ref is None:
        return model, {}
    conn_id, name = ref
    token = (settings.LLM_PROXY_INTERNAL_TOKEN or "").strip()
    if not token:
        raise ProviderConfigError(
            "company provider connections need LLM_PROXY_INTERNAL_TOKEN (backend and proxy)"
        )
    if company_id is None:
        raise ProviderConfigError(
            "a connection's model needs the company it belongs to"
        )
    from database import engine as default_engine
    from sqlmodel import Session

    with Session(engine or default_engine) as session:
        row = session.get(ProviderConnection, conn_id)
        if row is None or row.company_id != company_id:
            raise ProviderConfigError(
                f"provider connection {conn_id} is not this company's"
            )
        if not row.enabled:
            raise ProviderConfigError(f"provider connection {row.name!r} is disabled")
        key, headers = read_secrets(row)
        payload = {
            "name": row.name,
            "base_url": row.base_url,
            "api_key": key or "",
            "headers": headers,
        }
    encoded = base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")
    return name, {"X-Provider-Connection": encoded, "X-Internal-Token": token}
