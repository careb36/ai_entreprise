"""
Rework requests whose text names a missing module, package or import (issue #549).

The second measure #444 asked for beside the grounded check (#503): did the
rework requests that complain about a module or package that is not there
drop once the gate refuted invented dependencies before QA? This module says
which task events are rework requests and whether their text names a missing
module; ``GET /v1/analytics/missing_module_mentions`` splits the count at a
cutoff, by default the moment #503 merged.

What it counts is what the reviewers **wrote**, not what was true. The events
are observations (the backend wrote them, in the task's hash chain), but a
rework request's text is a reviewer's claim about the delivery: "No module
named x" may be wrong, and a missing module may go unmentioned. So the counts
are named for the text (``requests_whose_text_names_missing_module``), and
the gate's own send-backs, which the backend writes from what was observed at
delivery, are kept under their own source rather than mixed with the
reviewers'. The deliveries the gate refuted, by problem, are
``orchestration_metrics.count_gate_refutations``.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import datetime
from typing import Any

# Pull request #503 (the grounded check) merged 2026-11-12T12:33:51Z; naive
# UTC, as ``TaskEvent.created_at`` is stored.
GROUNDED_CHECK_SHIPPED_AT = datetime(2026, 11, 12, 12, 33, 51)

COUNTS_WHAT = (
    "Rework requests whose text names a missing module, package or import: "
    "what the reviewers wrote, not whether a module was in fact missing."
)

# The sources of a rework request. ``gate`` is the backend's own check before
# QA (author Verifier or Quality gate), ``person`` a person on the board or in
# a review, ``agent_reviewer`` any other role (QA, the Manager agent sending a
# task back for the client).
SOURCE_GATE = "gate"
SOURCE_PERSON = "person"
SOURCE_AGENT_REVIEWER = "agent_reviewer"

# Actors as ``services/tasks/task_events.py`` names them; platform may not
# import tasks, and the strings are the chain's own, so they never change.
_SYSTEM = "system"
_GATE = "gate"
_PERSON = "person"
_SEND_BACK_MARKER = "sent back to work"

_NOUN = r"(?:modules?|packages?|imports?|dependenc(?:y|ies)|libraries|library|crates?)"
_QUOTED = r"""(?:['"`][^'"`\n]{1,120}['"`])"""

# The matching rule: a text names a missing module, package or import when
# any of these match, ignoring case. Each one is the wording of a tool or of
# the gate, so a reviewer's paraphrase of an error is caught as well as the
# error pasted whole; ``tests/test_rework_mentions.py`` holds real-shaped
# texts that must and must not match.
MISSING_MODULE_RULES: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (name, re.compile(pattern, re.IGNORECASE))
    for name, pattern in (
        # Python
        ("python ModuleNotFoundError", r"\bModuleNotFoundError\b"),
        ("python ImportError", r"\bImportError\b"),
        ("python no module named", r"\bno module named\b"),
        # pip
        ("pip no matching distribution", r"\bno matching distribution found\b"),
        ("pip no satisfying version", r"\bcould not find a version that satisfies\b"),
        # Node, TypeScript, bundlers
        ("node cannot find module", r"\bcan(?:not|'t) find (?:module|package)\b"),
        ("bundler can't resolve", r"\bcan(?:not|'t) resolve\s+" + _QUOTED),
        ("npm not in registry", r"\bis not in (?:the|this) (?:npm )?registry\b"),
        # Go
        ("go no required module", r"\bno required module provides package\b"),
        # Rust
        ("rust unresolved import", r"\bunresolved imports?\b"),
        ("rust can't find crate", r"\bcan(?:not|'t) find crate\b"),
        # Java and Kotlin
        ("java package does not exist", r"\bpackage\s+[\w.]+\s+does not exist\b"),
        # The grounded check's own wording (``services/evidence/grounding.py``,
        # ``delivery_gate``'s problem name).
        ("gate invented dependency", r"\binvented dependency or import\b"),
        ("gate no registry has", r"\bno registry has\b"),
        ("gate import names no file", r"\bimports?\(s\) name no file\b"),
        # Plain words a reviewer uses.
        (
            "noun not found",
            r"\b" + _NOUN + r"(?:\s+" + _QUOTED + r")?\s+(?:is\s+|was\s+)?"
            r"(?:not found|does(?: not|n't) exist|not installed)\b",
        ),
        ("missing noun", r"\bmissing\s+(?:(?:a|an|the)\s+)?" + _NOUN + r"\b"),
        (
            "invented noun",
            r"\b(?:invented|hallucinated|non-?existent|unknown|unresolved)\s+"
            + _NOUN
            + r"\b",
        ),
    )
)


def names_missing_module(text: Any) -> bool:
    """True when *text* names a missing module, package or import.

    The rule is :data:`MISSING_MODULE_RULES`; a non-string never matches.
    """
    if not isinstance(text, str) or not text:
        return False
    return any(pattern.search(text) for _, pattern in MISSING_MODULE_RULES)


def rework_request_source(kind: Any, actor: Any, payload: Any) -> str | None:
    """Who asked for rework in this task event, or ``None`` if it is not a request.

    A rework request is:

    - a ``reviewed`` event whose ``payload.verdict`` is ``needs_work`` (QA's
      ``NEEDS WORK:``, the gate's send-back, a person's ``NEEDS WORK:`` note);
    - a person's ``reviewed`` event, the changes a person asked for in a
      review (``services/loop/run_review.py`` writes one only then);
    - a ``sent_back`` event by a person or the Manager agent, unless it
      belongs to a review (``payload.review_id``), whose reasons are in the
      ``reviewed`` event written with it.

    The system's notes (autopilot rework scheduled, a block) never count: they
    repeat a request, they do not make one.
    """
    if not isinstance(payload, dict):
        return None
    actor = str(actor or "").strip()
    if not actor or actor.lower() == _SYSTEM:
        return None
    if kind == "reviewed":
        if payload.get("verdict") != "needs_work" and actor != _PERSON:
            return None
    elif kind == "sent_back":
        text = str(payload.get("text") or "").strip().lower()
        if not text.startswith(_SEND_BACK_MARKER) or payload.get("review_id"):
            return None
    else:
        return None
    if actor == _GATE:
        return SOURCE_GATE
    if actor == _PERSON:
        return SOURCE_PERSON
    return SOURCE_AGENT_REVIEWER


def count_rework_mentions(events: Iterable[tuple[Any, Any, Any]]) -> dict[str, dict]:
    """Rework requests and those whose text names a missing module, by source.

    *events* are ``(kind, actor, payload)`` triples, read from the task-event
    chain. Both counts are keyed by :func:`rework_request_source`; a source
    with nothing is left out.
    """
    requests: dict[str, int] = {}
    mentions: dict[str, int] = {}
    for kind, actor, payload in events:
        source = rework_request_source(kind, actor, payload)
        if source is None:
            continue
        requests[source] = requests.get(source, 0) + 1
        if names_missing_module(payload.get("text")):
            mentions[source] = mentions.get(source, 0) + 1
    return {
        "rework_requests": dict(sorted(requests.items())),
        "requests_whose_text_names_missing_module": dict(sorted(mentions.items())),
    }
