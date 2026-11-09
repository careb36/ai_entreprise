import logging
import os
from enum import Enum
from typing import Any, Dict, Optional, Union

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from backend.llm.retry import (
    DEFAULT_BACKOFF_FACTOR,
    DEFAULT_INITIAL_DELAY,
    DEFAULT_MAX_DELAY,
    DEFAULT_MAX_RETRIES,
    ResilientChatModelWrapper,
    with_llm_retry,
    wrap_model_with_retry,
)

logger = logging.getLogger(__name__)


class AgentRole(str, Enum):
    ORCHESTRATOR = "Orchestrator"
    DEVELOPER = "Developer"
    QA = "QA"
    ARCHITECT = "Architect"
    PO = "PO"
    SCRUM_MASTER = "ScrumMaster"
    SECOPS = "SecOps"
    TECH_WRITER = "TechWriter"


class LLMConfig(BaseModel):
    model_name: str
    temperature: float = 0.2
    max_tokens: Optional[int] = None


class ModelFactory:
    ROLE_MODEL_MAP: Dict[AgentRole, LLMConfig] = {
        AgentRole.ORCHESTRATOR: LLMConfig(model_name="gemini-2.5-flash", temperature=0.1),
        AgentRole.DEVELOPER: LLMConfig(model_name="gemini-2.5-flash", temperature=0.2),
        AgentRole.QA: LLMConfig(model_name="gemini-2.5-flash", temperature=0.1),
        AgentRole.ARCHITECT: LLMConfig(model_name="gemini-2.5-flash", temperature=0.2),
        AgentRole.PO: LLMConfig(model_name="gemini-2.5-flash", temperature=0.3),
        AgentRole.SCRUM_MASTER: LLMConfig(model_name="gemini-2.5-flash", temperature=0.2),
        AgentRole.SECOPS: LLMConfig(model_name="gemini-2.5-flash", temperature=0.1),
        AgentRole.TECH_WRITER: LLMConfig(model_name="gemini-2.5-flash", temperature=0.3),
    }

    _model_cache: Dict[AgentRole, Optional[Union[ChatOpenAI, ResilientChatModelWrapper]]] = {}

    @staticmethod
    def model_for_role(role: AgentRole) -> str:
        """
        The model id to use for *role*.

        ``NEMOTRON_ROLES`` (comma-separated role names) opts roles into the
        NVIDIA models served by Nebius Token Factory; ``NEMOTRON_MODEL``
        (with ``NEMOTRON_MODEL_STRATEGIC`` / ``_FAST`` overrides) names them.
        Unset means the ``ROLE_MODEL_MAP`` default, so nothing changes for
        existing deployments.
        """
        default = ModelFactory.ROLE_MODEL_MAP[role].model_name
        opted_in = {
            r.strip().lower()
            for r in os.getenv("NEMOTRON_ROLES", "").split(",")
            if r.strip()
        }
        if role.value.lower() not in opted_in:
            return default
        tier_var = {
            AgentRole.PO: "NEMOTRON_MODEL_STRATEGIC",
            AgentRole.ARCHITECT: "NEMOTRON_MODEL_STRATEGIC",
            AgentRole.SECOPS: "NEMOTRON_MODEL_STRATEGIC",
            AgentRole.TECH_WRITER: "NEMOTRON_MODEL_FAST",
        }.get(role, "NEMOTRON_MODEL")
        return os.getenv(tier_var) or os.getenv("NEMOTRON_MODEL", "") or default

    @classmethod
    def get_model(cls, role: AgentRole) -> ResilientChatModelWrapper:
        """
        Return (or create and cache) the resilient ``ChatOpenAI`` client for *role*.

        The client is wrapped with ``ResilientChatModelWrapper`` providing exponential
        backoff retry handling for 502/504 timeouts.

        Args:
            role: The agent role whose model is needed.

        Returns:
            A resilient configured ChatOpenAI wrapper instance.
        """
        if role in cls._model_cache and cls._model_cache[role] is not None:
            return cls._model_cache[role]  # type: ignore

        config = cls.ROLE_MODEL_MAP.get(role, LLMConfig(model_name="gemini-2.5-flash"))
        proxy_url = os.getenv("LLM_PROXY_URL", "http://localhost:5100")
        api_key = os.getenv("OPENAI_API_KEY", "proxy-managed")

        raw_model = ChatOpenAI(
            model=cls.model_for_role(role),
            temperature=config.temperature,
            max_tokens=config.max_tokens,
            api_key=api_key,
            base_url=f"{proxy_url}/v1",
            default_headers={"X-Agent-Role": role.value},
            timeout=float(os.getenv("LLM_TIMEOUT", "120.0")),
        )

        resilient_model = wrap_model_with_retry(
            model=raw_model,
            max_retries=int(os.getenv("LLM_MAX_RETRIES", str(DEFAULT_MAX_RETRIES))),
            initial_delay=float(os.getenv("LLM_RETRY_INITIAL_DELAY", str(DEFAULT_INITIAL_DELAY))),
            max_delay=float(os.getenv("LLM_RETRY_MAX_DELAY", str(DEFAULT_MAX_DELAY))),
            backoff_factor=float(os.getenv("LLM_RETRY_BACKOFF_FACTOR", str(DEFAULT_BACKOFF_FACTOR))),
        )
        cls._model_cache[role] = resilient_model
        return resilient_model


# Role names stored on Agent rows that are not AgentRole values, keyed by their
# normalised form (lowercase, no spaces, dashes or underscores).
_ROLE_ALIASES: Dict[str, AgentRole] = {
    "reviewer": AgentRole.QA,
    "dev": AgentRole.DEVELOPER,
    "backenddev": AgentRole.DEVELOPER,
    "frontenddev": AgentRole.DEVELOPER,
    "fullstackdev": AgentRole.DEVELOPER,
    "mobiledev": AgentRole.DEVELOPER,
    "dataengineer": AgentRole.DEVELOPER,
    "mlengineer": AgentRole.DEVELOPER,
    "devops": AgentRole.DEVELOPER,
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
    """
    return ModelFactory.ROLE_MODEL_MAP[role_for_name(role)].model_name


def get_llm_for_role(role: str) -> ResilientChatModelWrapper:
    """
    Convenience wrapper that converts a role *string* to an ``AgentRole`` enum.

    Falls back to ``AgentRole.DEVELOPER`` for unknown role strings.
    """
    try:
        return ModelFactory.get_model(AgentRole(role))
    except Exception:
        return ModelFactory.get_model(AgentRole.DEVELOPER)


def create_completion(
    role: str,
    prompt: str,
    system_message: Optional[str] = None,
    bu_id: Optional[int] = None,
) -> str:
    """
    Execute a single-turn LLM completion for the given agent role with retry resilience.

    Args:
        role:           Agent role string (e.g. ``"Architect"``).
        prompt:         User-turn prompt text.
        system_message: Optional system instruction.
        bu_id:          Optional Business Unit ID for token quota enforcement.

    Returns:
        The model's text response as a plain string.

    Raises:
        RuntimeError: If all retries are exhausted or a non-retryable failure occurs.
    """
    try:
        model = get_llm_for_role(role)
    except Exception as e:
        raise RuntimeError(f"Failed to initialise LLM for role '{role}': {e}") from e

    messages = []
    if system_message:
        messages.append(SystemMessage(content=system_message))
    messages.append(HumanMessage(content=prompt))

    config: Dict[str, Any] = {}
    if bu_id:
        config["headers"] = {"X-Business-Unit-ID": str(bu_id)}

    try:
        response = model.invoke(messages, **config)
        return response.content if hasattr(response, "content") else str(response)
    except Exception as e:
        logger.error(
            "LLM completion failed permanently for role=%s bu_id=%s: %s",
            role,
            bu_id,
            e,
            exc_info=True,
        )
        raise RuntimeError(f"LLM call failed after retries: {e}") from e
