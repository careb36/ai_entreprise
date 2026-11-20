"""What this deployment can do with the configuration it has (issue #751).

The owner's standing decision (2026-11-20): La Fabrique runs on DeepSeek, and
no other configuration may stop the work. Every optional integration is
therefore either on, off with a named reason, or degraded with a named
reason; none of them refuses a start or blocks a sprint. This module names
them, once, for three readers:

- the startup self-check (``log_effective``), one log line per capability;
- ``/health/deep``'s ``capabilities`` section, beside the LLM proxy's own
  (``llm.providers``, ``llm.embeddings``, ``llm.failover``: what the proxy
  says of its keys, its embeddings and its failover);
- the Models page, which shows that section.

Only names and reasons leave here: a setting's value (a host, a key, an
address) never does, so the document can be served unauthenticated like the
rest of ``/health/deep``. Off by configuration is not a fault: no capability
changes the health status.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

ON, OFF, DEGRADED = "on", "off", "degraded"


def _cap(state: str, reason: str) -> dict[str, str]:
    return {"state": state, "reason": reason}


def _flag(enabled: bool, name: str, on_reason: str, off_reason: str) -> dict[str, str]:
    if enabled:
        return _cap(ON, f"{name}=true: {on_reason}")
    return _cap(OFF, f"{name}=false: {off_reason}")


def backend(
    settings: Any,
    *,
    memory_store: bool,
    graph_connected: bool,
    auth_configured: bool,
    encryption: bool,
    signing: bool,
    forge_ceiling: str,
) -> dict[str, dict[str, str]]:
    """The backend's capabilities, from its settings and what it observed at startup."""
    caps: dict[str, dict[str, str]] = {}
    caps["auth"] = (
        _cap(ON, "JWT_SECRET_KEY is set")
        if auth_configured
        else _cap(
            OFF,
            "JWT_SECRET_KEY is not set or too short: login answers 503 until it is set",
        )
    )
    caps["memory"] = (
        _cap(ON, "pgvector memory is kept; how it is embedded is llm.embeddings")
        if memory_store
        else _cap(
            OFF,
            "no vector_memory table (db/init-db/init.sql): memory search and store are off; "
            "agents run without it",
        )
    )
    if not settings.NEO4J_ENABLED:
        caps["knowledge_graph"] = _cap(
            OFF,
            "NEO4J_ENABLED=false: the graph is not recorded; memory uses "
            "pgvector alone",
        )
    elif graph_connected:
        caps["knowledge_graph"] = _cap(ON, "Neo4j connected")
    else:
        caps["knowledge_graph"] = _cap(
            DEGRADED,
            "NEO4J_ENABLED=true but Neo4j is not reachable: tried again "
            "in the background; nothing waits for it",
        )
    caps["github_token"] = (
        _cap(ON, "GITHUB_TOKEN is set: pull-request backfill is authenticated")
        if settings.GITHUB_TOKEN
        else _cap(
            OFF,
            "GITHUB_TOKEN is not set: pull-request backfill uses GitHub's unauthenticated rate "
            "limit; publishing uses the workspace daemon's own Git credentials",
        )
    )
    caps["forge"] = _cap(
        ON if forge_ceiling == "publish" else OFF,
        f"FORGE_POLICY_MAX={forge_ceiling}: the widest forge policy a company may choose; each company "
        "starts at none",
    )
    caps["mail"] = (
        _cap(ON, "SMTP_HOST is set: the Manager's brief is mailed")
        if (settings.SMTP_HOST or "").strip()
        else _cap(
            OFF,
            "SMTP_HOST is not set: the Manager's brief is not mailed; it stays in the inbox",
        )
    )
    caps["webhooks"] = _cap(
        ON,
        "per company: a company without a webhook is sent nothing, and a webhook "
        "that is down never holds up a sprint",
    )
    caps["sso"] = (
        _cap(
            ON,
            "per company: a company without an SSO connection signs in with passwords",
        )
        if auth_configured
        else _cap(OFF, "needs JWT_SECRET_KEY")
    )
    caps["error_reports"] = (
        _cap(ON, "ERROR_REPORT_URL is set: unhandled errors are posted there")
        if (settings.ERROR_REPORT_URL or "").strip()
        else _cap(OFF, "ERROR_REPORT_URL is not set: unhandled errors are logged only")
    )
    caps["research_tools"] = _flag(
        settings.RESEARCH_TOOLS_ENABLED,
        "RESEARCH_TOOLS_ENABLED",
        "public documentation hosts and registries, fetched without credentials; a host that does not "
        "answer is a tool error, not a failed task",
        "the deciding roles answer from their model alone",
    )
    caps["grounding_lookups"] = _flag(
        settings.GROUNDING_LOOKUPS_ENABLED,
        "GROUNDING_LOOKUPS_ENABLED",
        "package and signature lookups at the public registries",
        "no lookups",
    )
    hours = float(settings.MODELS_CATALOG_REFRESH_HOURS or 0)
    caps["model_catalog_refresh"] = (
        _cap(
            ON,
            f"MODELS_CATALOG_REFRESH_HOURS={hours:g}: refreshed from MODELS_CATALOG_URL (a GET, nothing "
            "sent); a failure keeps the bundled snapshot",
        )
        if hours > 0
        else _cap(OFF, "MODELS_CATALOG_REFRESH_HOURS=0: the bundled snapshot is used")
    )
    caps["console_assistants"] = _flag(
        bool(getattr(settings, "CONSOLE_ASSISTANTS_ENABLED", False)),
        "CONSOLE_ASSISTANTS_ENABLED",
        "a task may be sent for advice to an assistant CLI installed on the daemon's computer (GitHub "
        "Copilot CLI, Claude CLI), a provider other than DeepSeek; a missing CLI is skipped. Set it to "
        "false to keep every task on DeepSeek",
        "no assistant CLI is asked",
    )
    for name, setting in (
        ("manager_agent", "MANAGER_AGENT_ENABLED"),
        ("support_agent", "SUPPORT_AGENT_ENABLED"),
        ("reviewer_agent", "REVIEWER_AGENT_ENABLED"),
    ):
        caps[name] = _flag(
            bool(getattr(settings, setting)),
            setting,
            "runs on the default DeepSeek models",
            "not started",
        )
    caps["credential_encryption"] = (
        _cap(ON, "FERNET_SECRET_KEY is set")
        if encryption
        else _cap(
            DEGRADED,
            "FERNET_SECRET_KEY is not set: credentials are stored with a 'plaintext:' prefix",
        )
    )
    caps["audit_signing"] = (
        _cap(ON, "AUDIT_SIGNING_KEY is set")
        if signing
        else _cap(
            DEGRADED,
            "AUDIT_SIGNING_KEY is not set: audit rows are stored with an 'unsigned:' prefix",
        )
    )
    return caps


def from_state(state: Any) -> dict[str, dict[str, str]]:
    """``backend`` for the running app, from ``app.state`` and the settings."""
    from audit_signing import is_signing_enabled
    from auth_tokens import is_auth_configured
    from config import settings
    from crypto import is_encryption_enabled

    from services.platform import forge_policy_setting

    return backend(
        settings,
        memory_store=getattr(state, "memory_client", None) is not None,
        graph_connected=bool(getattr(state, "memory_graph", False)),
        auth_configured=is_auth_configured(),
        encryption=is_encryption_enabled(),
        signing=is_signing_enabled(),
        forge_ceiling=forge_policy_setting.ceiling(),
    )


# What the proxy's ``/health`` ``capabilities`` may carry here; anything else is dropped.
_PROXY_FIELDS = ("state", "reason", "provider", "model", "scope", "providers")


def from_proxy(raw: Any) -> dict[str, dict[str, Any]]:
    """The proxy's capabilities as ``llm.*`` entries; ``llm`` off when it said nothing."""
    if not isinstance(raw, dict):
        return {
            "llm": _cap(
                OFF,
                "the LLM proxy did not answer /health, or answered without capabilities",
            )
        }
    out: dict[str, dict[str, Any]] = {}
    configured = raw.get("providers_configured")
    if isinstance(configured, list):
        names = [str(p) for p in configured]
        if "deepseek" in names:
            out["llm.providers"] = _cap(ON, "configured: " + ", ".join(names))
        elif names:
            out["llm.providers"] = _cap(
                DEGRADED, "DEEPSEEK_API_KEY is not set; configured: " + ", ".join(names)
            )
        else:
            out["llm.providers"] = _cap(
                OFF,
                "no provider key is set: agent tasks fail until DEEPSEEK_API_KEY is",
            )
    for name in ("embeddings", "failover"):
        value = raw.get(name)
        if isinstance(value, dict):
            out[f"llm.{name}"] = {k: value[k] for k in _PROXY_FIELDS if k in value}
    return out


def with_memory_embedding(caps: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """``memory`` as the proxy embeds it (issue #755): on only with a semantic embedding.

    The table alone keeps memory searchable; what finds meaning is the
    embedding. With the proxy's local model (or a chosen Gemini) loaded,
    ``llm.embeddings`` is on and so is memory; with the hash vector standing
    in, memory is degraded with the proxy's reason. When the proxy said
    nothing, ``memory`` is left as the backend observed it.
    """
    memory, embeddings = caps.get("memory"), caps.get("llm.embeddings")
    if not memory or memory.get("state") != ON or not isinstance(embeddings, dict):
        return caps
    model = embeddings.get("model") or embeddings.get("provider") or "the proxy"
    if embeddings.get("state") == ON:
        caps["memory"] = _cap(
            ON, f"pgvector memory, embedded by {model} (llm.embeddings)"
        )
    else:
        caps["memory"] = _cap(
            DEGRADED,
            f"pgvector memory is kept, embedded by {model}: {embeddings.get('reason') or 'see llm.embeddings'}",
        )
    return caps


def log_effective(caps: dict[str, dict[str, Any]]) -> None:
    """The startup self-check: one line per capability, names and reasons only."""
    summary = ", ".join(
        f"{name}={cap.get('state')}" for name, cap in sorted(caps.items())
    )
    logger.info("Effective capabilities: %s", summary)
    for name, cap in sorted(caps.items()):
        if cap.get("state") != ON:
            logger.info(
                "capability %s: %s: %s", name, cap.get("state"), cap.get("reason")
            )
