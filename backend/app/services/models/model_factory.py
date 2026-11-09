"""
LLM Model Factory — multi-model LLM configuration and routing.

Maps each AI agent role to a specific Gemini or DeepSeek model configuration,
caches instantiated ``ChatOpenAI`` objects to avoid recreating HTTP sessions,
and exposes a convenience ``create_completion()`` function used by every
LangGraph node.

Model selection rationale:
- **PO / Architect / SecOps**: Gemini 2.5 Pro — best at natural language,
  architecture and security reasoning.
- **Developer / QA**: DeepSeek Coder — lower latency and cost for code-heavy
  tasks.
- **BackendDeveloper / FrontendDeveloper / DevOps / DataEngineer**: as the
  Developer (and ``AGENT_MODEL_DEVELOPER`` when they have no setting of their own).
- **Designer**: the deciding roles' model, for specifications and markup.
- **TechWriter / fallback**: Gemini 2.5 Flash — fast and fluent for
  documentation and general prompts.
- **EngramProcessor**: DeepSeek Coder — deterministic (low temperature) for
  structured JSON extraction from decision summaries.

All requests are routed through the internal LLM proxy (``llm-proxy/app.py``)
so API keys and token budgets are enforced centrally.
"""

import logging
import os
from enum import Enum
from typing import Any, ClassVar

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

logger = logging.getLogger(__name__)


class AgentRole(str, Enum):
    """Enumeration of all AI agent roles in the Fabrique workflow."""

    PO = "PO"
    ARCHITECT = "Architect"
    SCRUM_MASTER = "ScrumMaster"
    DEVELOPER = "Developer"
    QA = "QA"
    SECOPS = "SecOps"
    TECH_WRITER = "TechWriter"
    BACKEND_DEVELOPER = "BackendDeveloper"
    FRONTEND_DEVELOPER = "FrontendDeveloper"
    DEVOPS = "DevOps"
    DESIGNER = "Designer"
    DATA_ENGINEER = "DataEngineer"
    ENGRAM_PROCESSOR = "EngramProcessor"


class LLMConfig(BaseModel):
    """LLM model configuration bound to a specific agent role."""

    model_name: str
    temperature: float = 0.7
    max_tokens: int = 2048


class ModelFactory:
    """
    Factory that instantiates and caches ``ChatOpenAI`` clients per agent role.

    The ``_model_cache`` class variable is intentionally shared across all
    instances so that the same ``ChatOpenAI`` HTTP session is reused throughout
    the process lifetime, reducing connection overhead.
    """

    # Maps each role to its preferred model and generation parameters.
    # Using DeepSeek + Qwen Cloud (token plan) instead of Gemini.
    ROLE_MODEL_MAP: ClassVar[dict[AgentRole, LLMConfig]] = {
        AgentRole.PO: LLMConfig(
            model_name="qwen3.7-plus", temperature=0.3, max_tokens=4096
        ),
        AgentRole.ARCHITECT: LLMConfig(
            model_name="qwen3.7-plus", temperature=0.2, max_tokens=4096
        ),
        AgentRole.SCRUM_MASTER: LLMConfig(
            model_name="qwen3.7-plus", temperature=0.5, max_tokens=2048
        ),
        AgentRole.DEVELOPER: LLMConfig(
            model_name="deepseek-v4-pro", temperature=0.2, max_tokens=4096
        ),
        AgentRole.QA: LLMConfig(
            model_name="deepseek-v4-pro", temperature=0.2, max_tokens=4096
        ),
        AgentRole.SECOPS: LLMConfig(
            model_name="qwen3.7-plus", temperature=0.1, max_tokens=4096
        ),  # precision-critical
        AgentRole.TECH_WRITER: LLMConfig(
            model_name="qwen3.6-flash", temperature=0.4, max_tokens=4096
        ),
        # The specialised coding roles run what the Developer runs; the
        # Designer writes specifications and markup, as the deciding roles do.
        AgentRole.BACKEND_DEVELOPER: LLMConfig(
            model_name="deepseek-v4-pro", temperature=0.2, max_tokens=4096
        ),
        AgentRole.FRONTEND_DEVELOPER: LLMConfig(
            model_name="deepseek-v4-pro", temperature=0.2, max_tokens=4096
        ),
        AgentRole.DEVOPS: LLMConfig(
            model_name="deepseek-v4-pro", temperature=0.2, max_tokens=4096
        ),
        AgentRole.DATA_ENGINEER: LLMConfig(
            model_name="deepseek-v4-pro", temperature=0.2, max_tokens=4096
        ),
        AgentRole.DESIGNER: LLMConfig(
            model_name="qwen3.7-plus", temperature=0.4, max_tokens=4096
        ),
        AgentRole.ENGRAM_PROCESSOR: LLMConfig(
            model_name="deepseek-v4-pro", temperature=0.1, max_tokens=2048
        ),
    }

    # The coding roles that read the Developer's settings when they have none
    # of their own, so a deployment that set AGENT_MODEL_DEVELOPER keeps it.
    DEVELOPER_FAMILY = frozenset(
        {
            AgentRole.BACKEND_DEVELOPER,
            AgentRole.FRONTEND_DEVELOPER,
            AgentRole.DEVOPS,
            AgentRole.DATA_ENGINEER,
        }
    )

    # Class-level cache so each role is instantiated at most once per process.
    _model_cache: ClassVar[dict[AgentRole, ChatOpenAI | None]] = {}

    @staticmethod
    def model_for_role(role: AgentRole) -> str:
        """
        The model id to use for *role*, from the most specific setting to the
        least:

        1. ``AGENT_MODEL_<ROLE>`` (``AGENT_MODEL_PO``, ``AGENT_MODEL_ARCHITECT``,
           ``AGENT_MODEL_SCRUM_MASTER``, ``AGENT_MODEL_DEVELOPER``,
           ``AGENT_MODEL_QA``, ``AGENT_MODEL_SECOPS``,
           ``AGENT_MODEL_TECH_WRITER``, ``AGENT_MODEL_BACKEND_DEVELOPER``,
           ``AGENT_MODEL_FRONTEND_DEVELOPER``, ``AGENT_MODEL_DEVOPS``,
           ``AGENT_MODEL_DESIGNER``, ``AGENT_MODEL_DATA_ENGINEER``,
           ``AGENT_MODEL_ENGRAM_PROCESSOR``); a coding role without its own
           falls back to ``AGENT_MODEL_DEVELOPER``;
        2. ``NEMOTRON_ROLES``, for the roles it names: ``NEMOTRON_MODEL``
           (with ``NEMOTRON_MODEL_STRATEGIC`` / ``_FAST`` overrides);
        3. ``AGENT_MODEL``, for every role (one provider for the whole team);
        4. the ``ROLE_MODEL_MAP`` default.

        Unset means the default, so nothing changes for existing deployments.
        An agent's own ``model`` (the Agents page) still wins over all of it.
        """
        family = role in ModelFactory.DEVELOPER_FAMILY
        own = os.getenv(f"AGENT_MODEL_{role.name}", "").strip() or (
            os.getenv("AGENT_MODEL_DEVELOPER", "").strip() if family else ""
        )
        if own:
            return own
        default = ModelFactory.ROLE_MODEL_MAP[role].model_name
        opted_in = {
            r.strip().lower()
            for r in os.getenv("NEMOTRON_ROLES", "").split(",")
            if r.strip()
        }
        if role.value.lower() not in opted_in and not (
            family and "developer" in opted_in
        ):
            return os.getenv("AGENT_MODEL", "").strip() or default
        tier_var = {
            AgentRole.PO: "NEMOTRON_MODEL_STRATEGIC",
            AgentRole.ARCHITECT: "NEMOTRON_MODEL_STRATEGIC",
            AgentRole.SECOPS: "NEMOTRON_MODEL_STRATEGIC",
            AgentRole.TECH_WRITER: "NEMOTRON_MODEL_FAST",
            AgentRole.DESIGNER: "NEMOTRON_MODEL_STRATEGIC",
        }.get(role, "NEMOTRON_MODEL")
        return os.getenv(tier_var) or os.getenv("NEMOTRON_MODEL", "") or default

    @classmethod
    def get_model(cls, role: AgentRole) -> ChatOpenAI:
        """
        Return (or create and cache) the ``ChatOpenAI`` client for *role*.

        The client is configured to target the internal LLM proxy via
        ``base_url``.  The ``X-Agent-Role`` header lets the proxy apply the
        correct token budget for the calling agent.

        Args:
            role: The agent role whose model is needed.

        Returns:
            A configured ``ChatOpenAI`` instance.
        """
        if role in cls._model_cache:
            return cls._model_cache[role]

        config = cls.ROLE_MODEL_MAP.get(role, LLMConfig(model_name="gemini-2.5-flash"))
        proxy_url = os.getenv("LLM_PROXY_URL", "http://localhost:5100")

        model = ChatOpenAI(
            model=cls.model_for_role(role),
            temperature=config.temperature,
            max_tokens=config.max_tokens,
            # "proxy-managed" signals to the OpenAI SDK that auth is handled externally.
            api_key="proxy-managed",
            base_url=f"{proxy_url}/v1",
            # Forwarded to the proxy for per-role token budget tracking.
            default_headers={"X-Agent-Role": role.value},
        )
        cls._model_cache[role] = model
        return model


# Role names stored on Agent rows that are not AgentRole values, keyed by their
# normalised form (lowercase, no spaces, dashes or underscores).
_ROLE_ALIASES: dict[str, AgentRole] = {
    # The Reviewer agent judges deliverables, as QA does.
    "reviewer": AgentRole.QA,
    "dev": AgentRole.DEVELOPER,
    "backenddev": AgentRole.BACKEND_DEVELOPER,
    "frontenddev": AgentRole.FRONTEND_DEVELOPER,
    "fullstackdev": AgentRole.DEVELOPER,
    "mobiledev": AgentRole.DEVELOPER,
    "mlengineer": AgentRole.DEVELOPER,
    "data": AgentRole.DATA_ENGINEER,
    "sre": AgentRole.DEVOPS,
    "productowner": AgentRole.PO,
    "sm": AgentRole.SCRUM_MASTER,
    "security": AgentRole.SECOPS,
    "support": AgentRole.TECH_WRITER,
}


def _normalise_role(role: str) -> str:
    return "".join(ch for ch in (role or "").lower() if ch not in " -_")


def role_for_name(role: str) -> AgentRole:
    """
    Map a stored role string to an ``AgentRole``.

    Matches ``AgentRole`` values case-insensitively, then the aliases above;
    anything else is treated as ``AgentRole.DEVELOPER``, as
    ``get_llm_for_role`` does.
    """
    key = _normalise_role(role)
    for candidate in AgentRole:
        if _normalise_role(candidate.value) == key:
            return candidate
    return _ROLE_ALIASES.get(key, AgentRole.DEVELOPER)


def default_model_for_role(role: str) -> str:
    """
    The model id an agent of *role* uses when its own ``model`` is empty.

    This is the single source of role defaults for every caller (the task
    worker, the ScrumMaster, the Support and Reviewer agents, the PO's
    drafts, the LangGraph producer): ``ModelFactory.model_for_role``, so
    ``AGENT_MODEL`` and its per-role variants reach all of them.
    """
    return ModelFactory.model_for_role(role_for_name(role))


def get_llm_for_role(role: str) -> ChatOpenAI:
    """
    Convenience wrapper that converts a role *string* to an ``AgentRole`` enum.

    Falls back to ``AgentRole.DEVELOPER`` for unknown role strings to avoid
    hard failures in dynamic caller contexts (e.g. LangGraph state machines).

    Args:
        role: String role name (must match an ``AgentRole`` value).

    Returns:
        Configured ``ChatOpenAI`` instance.
    """
    try:
        return ModelFactory.get_model(AgentRole(role))
    except Exception:  # noqa: BLE001 - Any unknown role falls back to the Developer.
        # Unknown or invalid role — fall back to the Developer model.
        return ModelFactory.get_model(AgentRole.DEVELOPER)


def create_completion(
    role: str, prompt: str, system_message: str | None = None, bu_id: int | None = None
) -> str:
    """
    Execute a single-turn LLM completion for the given agent role with Governance.

    Args:
        role:           Agent role string (e.g. ``"Architect"``).
        prompt:         User-turn prompt text.
        system_message: Optional system instruction.
        bu_id:          Optional Business Unit ID for token quota enforcement.

    Returns:
        The model's text response as a plain string.

    Raises:
        RuntimeError: If the LLM proxy is unreachable or returns an error,
                      so callers can map it to a structured error state.
    """
    try:
        model = get_llm_for_role(role)
    except Exception as e:
        raise RuntimeError(f"Failed to initialise LLM for role '{role}': {e}") from e

    messages = []
    if system_message:
        messages.append(SystemMessage(content=system_message))
    messages.append(HumanMessage(content=prompt))

    config: dict[str, Any] = {}
    if bu_id:
        config["headers"] = {"X-Business-Unit-ID": str(bu_id)}

    try:
        return model.invoke(messages, **config).content
    except Exception as e:
        # Log with full traceback so the request ID shows up in the logs.
        logger.error(  # noqa: G201 - Kept as it was when the module moved (ADR 0016).
            "LLM completion failed for role=%s bu_id=%s: %s",
            role,
            bu_id,
            e,
            exc_info=True,
        )
        raise RuntimeError(
            f"LLM proxy error for role '{role}': {type(e).__name__}: {e}"
        ) from e
