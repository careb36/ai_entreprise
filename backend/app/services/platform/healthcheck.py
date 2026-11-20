"""Dependency probes for the deep healthcheck endpoint.

Each probe runs with a short timeout and returns a small dict so that
``/health/deep`` can aggregate component states without ever blocking the
request. The endpoint reports ``healthy`` only when every required component
is up; otherwise the overall status becomes ``degraded`` (or ``down`` if any
required component is missing).

Why deep healthchecks matter here
---------------------------------
La Fabrique runs several backing services that can fail independently
(NATS, Postgres, Neo4j, the LLM proxy). Without per-component visibility,
operators see "API is up" while agents silently stop progressing because
the broker disconnected or the proxy is stuck. The deep probe surfaces
the actual blast radius so GlassRoom and the runbook can react.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import httpx
from database import engine, get_neo4j_driver
from logging_config import logger
from sqlmodel import Session, text

from services.platform import capabilities
from services.platform.memory_graph import graph_enabled

_PROBE_TIMEOUT_SECONDS = 3.0


def _ok(detail: str | None = None, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"status": "up"}
    if detail:
        payload["detail"] = detail
    payload.update(extra)
    return payload


def _down(reason: str, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"status": "down", "reason": reason[:500]}
    payload.update(extra)
    return payload


async def probe_nats(nats_client: Any) -> dict[str, Any]:
    if not nats_client:
        return _down("not_initialized")
    nc = getattr(nats_client, "nc", None)
    if not nc:
        return _down("not_connected")
    try:
        is_connected = bool(nc.is_connected)
    except Exception as exc:  # noqa: BLE001  # defensive: SDK shape can change
        return _down(f"introspection_error: {exc}")
    if not is_connected:
        return _down("disconnected")
    return _ok(
        servers=getattr(nc, "connected_url", None) and str(nc.connected_url) or "?",
        subscriptions=len(getattr(nats_client, "subscriptions", {}) or {}),
    )


def _sync_probe_postgres() -> dict[str, Any]:
    try:
        with Session(engine) as session:
            session.exec(text("SELECT 1")).first()
        return _ok()
    except Exception as exc:  # noqa: BLE001
        return _down(f"{type(exc).__name__}: {exc}")


async def probe_postgres() -> dict[str, Any]:
    return await asyncio.to_thread(_sync_probe_postgres)


def _sync_probe_neo4j() -> dict[str, Any]:
    driver = None
    try:
        driver = get_neo4j_driver()
        with driver.session() as session:
            record = session.run("RETURN 1 AS ok").single()
            ok = record and record.get("ok") == 1
            return _ok() if ok else _down("unexpected_response")
    except Exception as exc:  # noqa: BLE001
        return _down(f"{type(exc).__name__}: {exc}")
    finally:
        if driver is not None:
            try:
                driver.close()
            except Exception:  # noqa: BLE001, S110
                pass


async def probe_neo4j() -> dict[str, Any]:
    # Off by configuration (the default) is not a fault: nothing is probed and
    # the component does not degrade the stack (issue #328).
    if not graph_enabled():
        return {"status": "disabled", "reason": "NEO4J_ENABLED=false"}
    return await asyncio.to_thread(_sync_probe_neo4j)


# The fields of the proxy's per-provider observation passed on as they are;
# anything else the proxy might add is dropped, so nothing more leaves here.
_PROVIDER_FIELDS = (
    "status",
    "observed_at",
    "http_status",
    "source",
    "checked",
    "detail",
)


def _provider_observations(raw: Any) -> dict[str, dict[str, Any]] | None:
    """The proxy's ``providers`` block, read-only: what it last saw of each provider.

    The proxy being up says nothing of whether a provider can serve (a key
    without credit, a placeholder key); this is the proxy's observation, not
    the backend's, and it does not change the component's status.
    """
    if not isinstance(raw, dict):
        return None
    out: dict[str, dict[str, Any]] = {}
    for name, obs in raw.items():
        if isinstance(obs, dict):
            out[str(name)] = {k: obs[k] for k in _PROVIDER_FIELDS if k in obs}
    return out


async def probe_llm_proxy() -> dict[str, Any]:
    base_url = os.getenv("LLM_PROXY_URL", "http://localhost:5100").rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT_SECONDS) as client:
            response = await client.get(f"{base_url}/health")
        if response.status_code != 200:
            return _down(f"http_{response.status_code}")
        try:
            body = response.json()
        except Exception:  # noqa: BLE001
            body = {}
        models = body.get("models")
        extra: dict[str, Any] = {}
        providers = _provider_observations(body.get("providers"))
        if providers is not None:
            extra["providers"] = providers
        if isinstance(body.get("capabilities"), dict):
            extra["capabilities"] = body["capabilities"]
        return _ok(
            detail=body.get("status"),
            models_count=len(models) if isinstance(models, list) else None,
            **extra,
        )
    except Exception as exc:  # noqa: BLE001
        return _down(f"{type(exc).__name__}: {exc}")


SEVERITY_ORDER = ("healthy", "degraded", "down")


def worst(*severities: str) -> str:
    """The most severe of *severities*."""
    return max(severities, key=SEVERITY_ORDER.index)


def verification(tests_enabled: bool, sandbox_default: str) -> dict[str, Any]:
    """Whether the deployment can verify what agents deliver (issue #766).

    With tests off no code delivery can be ``verified``; with the host
    sandbox agent-written code runs without isolation. Either is
    ``degraded`` with the reason, whatever a company acknowledged: the
    acknowledgement lets its sprints run, it does not make the guarantee
    whole (``services/platform/acknowledgements.py``).
    """
    mode = str(sandbox_default or "host").strip().lower()
    reasons = []
    if not tests_enabled:
        reasons.append(
            "tests are off (DELIVERY_TESTS_ENABLED): no code delivery can be verified"
        )
    if mode == "host":
        reasons.append(
            "the default sandbox is host (DELIVERY_SANDBOX): agent code runs "
            "without isolation"
        )
    return {
        "status": "degraded" if reasons else "healthy",
        "tests": "on" if tests_enabled else "off",
        "sandbox_default": mode,
        "reasons": reasons,
    }


async def aggregate(
    nats_client: Any,
    nats_state: str | None = None,
    backend_capabilities: dict[str, dict[str, Any]] | None = None,
    verification_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run all probes concurrently and return a structured health document.

    ``nats_state`` is the backend's NATS connection lifecycle state
    (``"connecting"`` while the startup connector is still retrying,
    ``"connected"`` once workers have started); it is reported as the
    top-level ``nats`` field when given. ``backend_capabilities``
    (``capabilities.from_state``), when given, is reported with the LLM
    proxy's as the top-level ``capabilities`` section (issue #751); it never
    changes the status.
    """
    nats_result, postgres_result, neo4j_result, llm_result = await asyncio.gather(
        probe_nats(nats_client),
        probe_postgres(),
        probe_neo4j(),
        probe_llm_proxy(),
    )

    components = {
        "nats": nats_result,
        "postgres": postgres_result,
        "neo4j": neo4j_result,
        "llm_proxy": llm_result,
    }

    # Postgres and the LLM proxy are required for any agent work. Without NATS
    # we can still serve API reads but cannot run sprints. Without Neo4j we lose
    # episodic memory but kickoff still works; Neo4j disabled by configuration
    # is reported as such and degrades nothing. Surface the nuance instead of a
    # single boolean so operators can read the reason at a glance.
    required = {"postgres", "llm_proxy"}
    severity = "healthy"
    for name, comp in components.items():
        if comp.get("status") not in ("up", "disabled"):
            if name in required:
                severity = "down"
                break
            severity = "degraded"

    if verification_state is not None:
        severity = worst(severity, verification_state["status"])

    logger.info(
        "deep healthcheck: severity=%s components=%s",
        severity,
        {k: v.get("status") for k, v in components.items()},
    )
    document: dict[str, Any] = {"status": severity, "components": components}
    if verification_state is not None:
        document["verification"] = verification_state
    if nats_state is not None:
        document["nats"] = nats_state
    if backend_capabilities is not None:
        document["capabilities"] = capabilities.with_memory_embedding(
            {
                **backend_capabilities,
                **capabilities.from_proxy(llm_result.pop("capabilities", None)),
            }
        )
    return document
