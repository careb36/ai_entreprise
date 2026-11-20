"""
Which provider and model serve a request, and where it goes when that fails.

A requested id is served only by the provider its prefix names, and only if
that provider's catalogue has it (a deprecated Gemini id is aliased to its
replacement). When the chosen provider answers 429 or 5xx, the provider's
other model is tried first (DeepSeek Pro and Flash stand in for each other),
then one catalogue model per other provider, but only for the providers the
operator named in ``LLM_FALLBACK_PROVIDERS`` (issue #751): by default a request
never leaves the provider it was sent to, so a key configured for one role
does not make that provider a processor of every other role's prompts.

The functions take the catalogue as a callable (provider -> routable ids) and
the provider call as a callable, so ``app`` passes its own live state and the
tests that patch it there are seen here.
"""

import logging
import time
from collections.abc import Callable

import upstream
from catalogue import (
    GEMINI_ALIASES,
    GEMINI_DEPRECATED,
    PROVIDER_PREFIXES,
    provider_for_model,
)

logger = logging.getLogger("llm-proxy")

Catalogue = Callable[[str], set[str]]

# Preference order (most preferred first) when the exact model is not in the catalogue.
PREFERENCE_TIERS = {
    "fast":     ["gemini-3.8-flash", "gemini-flash-latest", "gemini-3.1-flash-lite",
                 "gemini-2.5-flash-lite", "gemini-2.0-flash-lite"],
    "powerful": ["gemini-3.8-flash", "gemini-3.1-pro-preview", "gemini-pro-latest",
                 "gemini-2.5-pro"],
}

# The provider's own other model, tried first after a 429/5xx (the failed model
# is skipped); used only if it is in the provider's catalogue.
SAME_PROVIDER_ALTERNATES: dict[str, list[str]] = {
    "deepseek": ["deepseek-flash", "deepseek-v4-pro"],
}

# Where a request goes next, in order, when the operator allowed that provider
# (``LLM_FALLBACK_PROVIDERS``).  Each candidate is used only if it is in that
# provider's catalogue.
FALLBACK_CANDIDATES: list[tuple[str, list[str]]] = [
    ("deepseek", ["deepseek-flash"]),
    ("qwen", ["qwen3.6-flash"]),
    ("gemini", PREFERENCE_TIERS["fast"]),
]


def available_ids(catalogue: Catalogue) -> list[str]:
    """Every model id the chat endpoint routes, as ``/v1/models`` lists them."""
    ids: set[str] = set()
    for provider in PROVIDER_PREFIXES:
        routable = catalogue(provider)
        if provider == "gemini":
            routable -= GEMINI_DEPRECATED
        ids |= routable
    return sorted(ids)


def resolve(requested: str, catalogue: Catalogue) -> tuple[str, str] | None:
    """
    Return ``(provider, model_id)`` for *requested*, or ``None`` when no
    provider serves it.  A deprecated Gemini id is aliased to its replacement
    when the replacement is in the catalogue.  Nothing else is substituted:
    an id that is not in its provider's catalogue is unknown.
    """
    provider = provider_for_model(requested)
    if provider is None:
        return None
    routable = catalogue(provider)
    if provider == "gemini":
        alias = GEMINI_ALIASES.get(requested)
        if alias and alias in routable:
            return provider, alias
    if requested in routable:
        return provider, requested
    return None


def parse_fallback_providers(raw: str) -> tuple[str, ...]:
    """The providers ``LLM_FALLBACK_PROVIDERS`` names, in order; unknown names dropped and logged."""
    known = [provider for provider, _ in FALLBACK_CANDIDATES]
    named = [part.strip().lower() for part in (raw or "").split(",") if part.strip()]
    unknown = sorted(set(named) - set(known))
    if unknown:
        logger.warning("LLM_FALLBACK_PROVIDERS names unknown providers, ignored: %s", ", ".join(unknown))
    return tuple(dict.fromkeys(p for p in named if p in known))


def failover_scope(cross_providers: tuple[str, ...]) -> dict:
    """What ``/health`` says of the failover: where a failed request may move."""
    if not cross_providers:
        return {"state": "on", "scope": "same_provider",
                "reason": "after a 429/5xx a request moves only to the same provider's other model "
                          "(DeepSeek Pro and Flash); LLM_FALLBACK_PROVIDERS is not set"}
    return {"state": "on", "scope": "cross_provider", "providers": list(cross_providers),
            "reason": "after a 429/5xx a request may also move to: " + ", ".join(cross_providers)
                      + " (LLM_FALLBACK_PROVIDERS)"}


def fallback_targets(
    failed_provider: str,
    catalogue: Catalogue,
    *,
    failed_model: str = "",
    cross_providers: tuple[str, ...] = (),
) -> list[tuple[str, str]]:
    """Catalogue models to try after a 429/5xx: the same provider's other model,
    then one per provider the operator allowed in *cross_providers*."""
    targets = []
    own = catalogue(failed_provider)
    for candidate in SAME_PROVIDER_ALTERNATES.get(failed_provider, []):
        if candidate != failed_model and candidate in own:
            targets.append((failed_provider, candidate))
            break
    for provider, candidates in FALLBACK_CANDIDATES:
        if provider == failed_provider or provider not in cross_providers:
            continue
        routable = catalogue(provider)
        for candidate in candidates:
            if candidate in routable:
                targets.append((provider, candidate))
                break
    return targets


def complete_with_fallback(
    provider: str,
    model: str,
    data: dict,
    *,
    call: Callable[[str, str, dict, float], tuple[dict, int]],
    targets: Callable[[str, str], list[tuple[str, str]]],
    budget: float,
) -> tuple[dict, int, str, str, dict | None]:
    """
    Call the resolved model; on 429/5xx try what *targets* names for the
    failed provider and model, within the same time budget.  Returns
    ``(result, tokens, served_provider, served_model, fallback_from)`` or
    raises the primary ``UpstreamError`` with every attempt attached.
    """
    deadline = time.monotonic() + budget
    try:
        result, tokens = call(provider, model, data, budget)
        return result, tokens, provider, model, None
    except upstream.UpstreamError as primary:
        if not primary.retriable:
            raise
        logger.warning("%s; trying fallback models from the catalogue", primary)
        primary.attempts = [primary.as_dict()]
        for fb_provider, fb_model in targets(provider, model):
            remaining = deadline - time.monotonic()
            if remaining < 5:
                break
            try:
                result, tokens = call(fb_provider, fb_model, data, remaining)
            except upstream.UpstreamError as exc:
                logger.warning("Fallback failed: %s", exc)
                primary.attempts.append(exc.as_dict())
                if not exc.retriable:
                    break
                continue
            return result, tokens, fb_provider, fb_model, upstream.fallback_record(primary)
        raise primary
