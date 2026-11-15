"""The routing policy: difficulty bands → a model or a proxy tier (issue #319).

A policy is data a person writes, never a model's choice::

    {"bands": {"low": "<cheaper model>", "medium": "fast", "high": "<stronger model>"},
     "label": "cheap-easy"}

A band key is a difficulty band of ``services/models/difficulty.py``
(``low``, ``medium``, ``high``) or a range of scores (``"1-2"``, ``"3-5"``, the
form the first policies used); a value is a model id, one of the company's
connections (``conn:<id>:<model>``) or a proxy tier name (``fast``,
``powerful``…), which the proxy does not serve as a model, so the role default
runs it and the routing records why. ``label`` is optional: the name the
routing record and the bench show; without one, a short fingerprint of the
bands.

Where it comes from, most specific first: the company's (``Company.model_policy``,
written by a Manager through ``PUT /v1/companies/{id}/model_settings``), then
the deployment's ``AGENT_MODEL_POLICY``. Neither set: no routing, the role
default, as before the policy existed. A stored or configured policy that
cannot be read is ignored with a warning, never half-applied as something
else; a band that cannot be read is dropped with a warning and the others
kept. A Manager's write is checked strictly (``validate``): a bad policy is a
422, never stored.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from services.models import difficulty

logger = logging.getLogger("fabrique-task-worker")

TIERS = frozenset({"fast", "balanced", "code", "strategic", "powerful", "preview"})
MAX_BANDS = 10
MAX_VALUE_CHARS = 200
MAX_LABEL_CHARS = 60
COMPANY, DEPLOYMENT = "company", "deployment"
_warned: set[str] = set()


class InvalidPolicy(ValueError):
    """A policy a Manager cannot store; the message says why."""


def _warn_once(message: str, *args: Any) -> None:
    key = message % args
    if key not in _warned:
        _warned.add(key)
        logger.warning(message, *args)


def band_range(key: Any) -> tuple[int, int] | None:
    """The scores a band key holds (``"low"`` → (1, 2), ``"3-5"`` → (3, 5)), or None."""
    text = str(key or "").strip().lower()
    if text in difficulty.BAND_RANGES:
        return difficulty.BAND_RANGES[text]
    lo, sep, hi = text.partition("-")
    if not sep or not lo.strip().isdigit() or not hi.strip().isdigit():
        return None
    low, high = int(lo), int(hi)
    if not difficulty.MIN_SCORE <= low <= high <= difficulty.MAX_SCORE:
        return None
    return low, high


def is_tier(value: str) -> bool:
    return value.strip().lower() in TIERS


def _value_problem(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return "a model id or a tier name"
    if len(value) > MAX_VALUE_CHARS or any(ch in value for ch in "\r\n\t"):
        return "not a model id"
    return None


def _label(bands: dict[str, str], label: Any) -> str:
    text = str(label or "").strip()[:MAX_LABEL_CHARS]
    if text:
        return text
    digest = hashlib.sha256(
        json.dumps(bands, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"policy-{digest[:8]}"


def parse(value: Any, source: str = DEPLOYMENT) -> dict[str, Any]:
    """*value* (a JSON string or a dict) as ``{"bands", "label"}``; ``{}`` when
    there is none or it cannot be read, with a warning for the latter."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return {}
    data = value
    if isinstance(value, str):
        try:
            data = json.loads(value)
        except (ValueError, TypeError):
            _warn_once("Routing policy (%s) is not JSON; ignored", source)
            return {}
    if not isinstance(data, dict) or not isinstance(data.get("bands"), dict):
        _warn_once('Routing policy (%s) has no "bands" object; ignored', source)
        return {}
    bands: dict[str, str] = {}
    for key, target in list(data["bands"].items())[:MAX_BANDS]:
        if band_range(key) is None or _value_problem(target):
            _warn_once(
                "Routing policy (%s): band %r → %r cannot be read; dropped",
                source,
                key,
                target,
            )
            continue
        bands[str(key).strip().lower()] = target.strip()
    if not bands:
        _warn_once("Routing policy (%s) has no readable band; ignored", source)
        return {}
    return {"bands": bands, "label": _label(bands, data.get("label"))}


def validate(value: Any) -> dict[str, Any] | None:
    """A Manager's policy, checked strictly: ``None`` to clear, else the
    normalised policy. Raises ``InvalidPolicy`` with the reason."""
    if value is None or value == {} or value == "":
        return None
    if not isinstance(value, dict):
        raise InvalidPolicy('model_policy: an object with "bands"')
    unknown = set(value) - {"bands", "label"}
    if unknown:
        raise InvalidPolicy(f"model_policy: unknown field {min(unknown)!r}")
    bands = value.get("bands")
    if not isinstance(bands, dict) or not bands:
        raise InvalidPolicy("model_policy.bands: at least one band")
    if len(bands) > MAX_BANDS:
        raise InvalidPolicy(f"model_policy.bands: at most {MAX_BANDS}")
    clean: dict[str, str] = {}
    for key, target in bands.items():
        if band_range(key) is None:
            raise InvalidPolicy(
                f"model_policy.bands: {key!r} is not low, medium, high or a range such as 1-2"
            )
        problem = _value_problem(target)
        if problem:
            raise InvalidPolicy(f"model_policy.bands.{key}: {problem}")
        clean[str(key).strip().lower()] = target.strip()
    label = value.get("label")
    if label is not None and not isinstance(label, str):
        raise InvalidPolicy("model_policy.label: a short text")
    out: dict[str, Any] = {"bands": clean}
    if label and label.strip():
        out["label"] = label.strip()[:MAX_LABEL_CHARS]
    return out


def deployment_policy() -> dict[str, Any]:
    """The deployment's ``AGENT_MODEL_POLICY``, parsed; ``{}`` when none or unreadable."""
    from config import settings

    return parse(getattr(settings, "AGENT_MODEL_POLICY", "") or "", DEPLOYMENT)


def company_policy(company: Any) -> dict[str, Any]:
    """The company's own policy, parsed; ``{}`` when none or unreadable."""
    stored = getattr(company, "model_policy", None) if company is not None else None
    return parse(stored, COMPANY) if stored else {}


def effective(
    session: Any, company_id: int | None
) -> tuple[dict[str, Any], str | None]:
    """The policy a company's tasks run under and where it came from:
    ``(policy, "company")``, ``(policy, "deployment")`` or ``({}, None)``."""
    if session is not None and company_id is not None:
        from models import Company

        own = company_policy(session.get(Company, company_id))
        if own:
            return own, COMPANY
    deployed = deployment_policy()
    return (deployed, DEPLOYMENT) if deployed else ({}, None)
