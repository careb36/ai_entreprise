"""A model's profile: what it can take and return, and the loop's limits for it.

Before this module the catalogue knew each model's context window, output
limit and whether it calls tools, but only the model suggestions read it;
the Developer's tool rounds, its tool results' size and its answer's cap
were one value for every model (issue #766). After the OpenHands SDK's
``LLM`` (capabilities from a registry, a caller's overrides first, and the
source of each recorded) and its named ``[llm.<name>]`` profiles, a profile
is built from four sources, the first that has a field winning:

1. the company's limits (``companies.limits``, set by an Owner within the
   ceilings of ``services/contracts/company_limits.py``), for the limits;
2. ``MODEL_PROFILES`` (a setting: a JSON object by model name), the
   operator's overrides, e.g.
   ``{"qwen4-max": {"context": 262144, "output": 65536, "tool_rounds": 12}}``;
3. the catalogue (``model_catalog``, models.dev), for the capabilities;
4. the deployment's settings for the limits (``DEVELOPER_TOOL_ROUNDS``,
   ``MCP_RESULT_MAX_CHARS``, ``DEVELOPER_MAX_OUTPUT_TOKENS``) and
   ``DEFAULT_CAPABILITIES`` for a model nobody describes.

``sources`` says which one decided each field, so a run's evidence can cite
the configuration it ran under. The limits stay within ``CEILINGS``, the
contract's: an operator or an Owner chooses inside a range the code holds
(AixleHQ/flow's ``MAX_RETRIES``), and a malformed override is ignored and
logged rather than lifting a limit (Orca's rule). Nothing an agent can
reach sets a profile: only the deployment's settings and an Owner do.
"""

from __future__ import annotations

import logging
from typing import Any, Final

from config import settings

from services.contracts import company_limits
from services.models import model_catalog

logger = logging.getLogger(__name__)

# What a model nobody describes is taken to have: small enough to be safe.
DEFAULT_CAPABILITIES: Final[dict[str, Any]] = {
    "context": 131072,
    "output": 8192,
    "tool_call": True,
    "reasoning": False,
}
CAPABILITIES: Final = tuple(DEFAULT_CAPABILITIES)
LIMITS: Final = ("tool_rounds", "result_max_chars", "max_output_tokens")
# The most anyone may set; above it a value is lowered to it.
CEILINGS: Final[dict[str, int]] = {key: company_limits.CEILINGS[key] for key in LIMITS}
# The catalogue's provider tried first for a model name's prefix.
PROVIDER_FOR_PREFIX: Final = (("deepseek", "deepseek"), ("qwen", "alibaba"))


def bare_name(model: str | None) -> str:
    """The model's own name: ``conn:<id>:<model>`` (a company connection) is
    ``<model>``."""
    name = (model or "").strip()
    if name.startswith("conn:") and name.count(":") >= 2:
        return name.split(":", 2)[2]
    return name


def _catalog_entry(name: str) -> tuple[str | None, dict[str, Any] | None]:
    providers = model_catalog.catalog().get("providers") or {}
    preferred = [pid for prefix, pid in PROVIDER_FOR_PREFIX if name.startswith(prefix)]
    for pid in [*preferred, *sorted(providers)]:
        entry = ((providers.get(pid) or {}).get("models") or {}).get(name)
        if entry:
            return pid, entry
    return None, None


def _valid(key: str, value: Any) -> bool:
    if key in {"tool_call", "reasoning"}:
        return isinstance(value, bool)
    if key == "max_output_tokens":  # 0: the provider's own default
        return isinstance(value, int) and not isinstance(value, bool) and value >= 0
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def overrides(name: str) -> dict[str, Any]:
    """The operator's overrides for *name*, without the malformed ones."""
    given = (settings.MODEL_PROFILES or {}).get(name) or {}
    if not isinstance(given, dict):
        logger.warning("MODEL_PROFILES[%r] is not an object: ignored", name)
        return {}
    kept = {}
    for key, value in given.items():
        if key in (*CAPABILITIES, *LIMITS) and _valid(key, value):
            kept[key] = value
        else:
            logger.warning(
                "MODEL_PROFILES[%r][%r]=%r is not valid: ignored", name, key, value
            )
    return kept


def _setting_limits() -> dict[str, int]:
    return {
        "tool_rounds": int(settings.DEVELOPER_TOOL_ROUNDS),
        "result_max_chars": int(settings.MCP_RESULT_MAX_CHARS),
        "max_output_tokens": max(0, int(settings.DEVELOPER_MAX_OUTPUT_TOKENS)),
    }


def profile(model: str | None, company: dict[str, Any] | None = None) -> dict[str, Any]:
    """The profile of *model* for a company whose limits are *company*: its
    capabilities, the Developer's limits, and ``sources`` (``company``,
    ``override``, ``catalog:<provider>``, ``setting`` or ``default``) for
    each field."""
    own = company_limits.parse(company)
    name = bare_name(model)
    given = overrides(name)
    pid, entry = _catalog_entry(name)
    out: dict[str, Any] = {"model": name}
    sources: dict[str, str] = {}
    for key in CAPABILITIES:
        if key in given:
            out[key], sources[key] = given[key], "override"
        elif entry and entry.get(key) is not None:
            out[key], sources[key] = entry[key], f"catalog:{pid}"
        else:
            out[key], sources[key] = DEFAULT_CAPABILITIES[key], "default"
    for key, value in _setting_limits().items():
        if key in own:
            out[key], sources[key] = own[key], "company"
        elif key in given:
            out[key], sources[key] = given[key], "override"
        else:
            out[key], sources[key] = value, "setting"
        out[key] = min(out[key], CEILINGS[key])
    # The answer's cap never asks for more than the model returns.
    if out["max_output_tokens"] and out["output"]:
        out["max_output_tokens"] = min(out["max_output_tokens"], int(out["output"]))
    out["sources"] = sources
    return out
