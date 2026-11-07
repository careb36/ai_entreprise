"""The deterministic check of a specification, and its numbering.

No model decides whether a specification is ready: this module does, the
same way every time. ``normalize`` gives the requirements, criteria and
quality attributes their ids in order (``REQ-1``, ``REQ-1.2``, ``NFR-1``)
and trims every text to its limit, so an id is never trusted from a draft.
``check`` lists what stops an approval (``blocking``) and what a person
should look at (``warning``): a requirement without a testable criterion,
a criterion with an empty step, a quality attribute without a number, a
question nobody answered, a word that promises without measuring.
"""

from __future__ import annotations

import re
from typing import Any

MAX_REQUIREMENTS = 60
MAX_CRITERIA = 10
MAX_QUALITY = 20
MAX_CONSTRAINTS = 20
MAX_QUESTIONS = 20
TEXT_LIMIT = 1000
TITLE_LIMIT = 200
MIN_DESCRIPTION_CHARS = 20

# Words that promise a property without saying how it is measured. A text
# that also carries a number is left alone: "under 200 ms" is not vague.
VAGUE_WORDS = (
    "fast",
    "quick",
    "quickly",
    "easy",
    "easily",
    "simple",
    "intuitive",
    "user-friendly",
    "user friendly",
    "robust",
    "scalable",
    "efficient",
    "performant",
    "responsive",
    "seamless",
    "flexible",
    "reliable",
    "etc",
    "and so on",
    "tbd",
    "rápido",
    "rápida",
    "fácil",
    "simple",
    "intuitivo",
    "intuitiva",
    "amigable",
    "robusto",
    "robusta",
    "escalable",
    "eficiente",
    "flexible",
    "confiable",
    "etcétera",
)
_VAGUE = re.compile(
    r"(?<![\w-])("
    + "|".join(re.escape(w) for w in dict.fromkeys(VAGUE_WORDS))
    + r")(?![\w-])",
    re.IGNORECASE,
)
_DIGIT = re.compile(r"\d")


def _text(value: Any, limit: int = TEXT_LIMIT) -> str:
    return " ".join(str(value if value is not None else "").split())[:limit]


def _items(value: Any, limit: int) -> list[Any]:
    return list(value)[:limit] if isinstance(value, list | tuple) else []


def normalize(spec: dict[str, Any]) -> dict[str, Any]:
    """The specification's content with ids assigned in order and texts trimmed.

    Takes and returns plain dicts (``title``, ``summary``, ``requirements``,
    ``quality``, ``constraints``, ``questions``); what a draft or a form
    carried as ids is discarded. An answered question keeps its answer.
    """
    requirements = []
    for number, raw in enumerate(_items(spec.get("requirements"), MAX_REQUIREMENTS), 1):
        if not isinstance(raw, dict):
            continue
        rid = f"REQ-{number}"
        criteria = []
        for sub, c in enumerate(_items(raw.get("criteria"), MAX_CRITERIA), 1):
            if not isinstance(c, dict):
                continue
            criteria.append(
                {
                    "id": f"{rid}.{sub}",
                    "given": _text(c.get("given")),
                    "when": _text(c.get("when")),
                    "then": _text(c.get("then")),
                }
            )
        requirements.append(
            {
                "id": rid,
                "title": _text(raw.get("title"), TITLE_LIMIT),
                "description": _text(raw.get("description")),
                "criteria": criteria,
            }
        )
    quality = [
        {
            "id": f"NFR-{number}",
            "attribute": _text(q.get("attribute"), TITLE_LIMIT),
            "target": _text(q.get("target")),
        }
        for number, q in enumerate(_items(spec.get("quality"), MAX_QUALITY), 1)
        if isinstance(q, dict)
    ]
    constraints = [
        _text(c) for c in _items(spec.get("constraints"), MAX_CONSTRAINTS) if _text(c)
    ]
    questions = []
    for q in _items(spec.get("questions"), MAX_QUESTIONS):
        if isinstance(q, dict):
            question, answer = _text(q.get("question")), _text(q.get("answer"))
        else:
            question, answer = _text(q), ""
        if question:
            questions.append({"question": question, "answer": answer or None})
    return {
        "title": _text(spec.get("title"), TITLE_LIMIT),
        "summary": _text(spec.get("summary"), 4000),
        "requirements": requirements,
        "quality": quality,
        "constraints": constraints,
        "questions": questions,
    }


def criterion_ids(spec: dict[str, Any]) -> list[str]:
    """Every criterion id of *spec*, in order."""
    return [
        c["id"]
        for r in spec.get("requirements") or []
        for c in r.get("criteria") or []
        if isinstance(c, dict) and c.get("id")
    ]


def _finding(severity: str, where: str, code: str, message: str) -> dict[str, str]:
    return {"severity": severity, "where": where, "code": code, "message": message}


def _vague(text: str) -> str | None:
    if _DIGIT.search(text):
        return None
    match = _VAGUE.search(text)
    return match.group(1) if match else None


def check(spec: dict[str, Any]) -> dict[str, Any]:
    """What keeps a normalized specification from approval, and what to look at.

    Returns ``{ok, blocking, warnings, findings}``; ``ok`` is true when
    nothing blocks. Each finding names where it is (``REQ-2.1``, ``NFR-1``,
    ``Q3``, or ``spec``), a stable ``code`` and a message.
    """
    findings: list[dict[str, str]] = []
    requirements = spec.get("requirements") or []
    if not spec.get("title"):
        findings.append(
            _finding("blocking", "spec", "no_title", "The specification has no title.")
        )
    if not requirements:
        findings.append(
            _finding(
                "blocking",
                "spec",
                "no_requirements",
                "The specification lists no requirement.",
            )
        )
    for r in requirements:
        rid = r.get("id", "REQ-?")
        if not r.get("title"):
            findings.append(
                _finding(
                    "blocking", rid, "empty_title", "The requirement has no title."
                )
            )
        description = r.get("description") or ""
        if len(description) < MIN_DESCRIPTION_CHARS:
            findings.append(
                _finding(
                    "warning",
                    rid,
                    "short_description",
                    f"The description has under {MIN_DESCRIPTION_CHARS} characters; say what is expected and why.",
                )
            )
        if word := _vague(f"{r.get('title', '')} {description}"):
            findings.append(
                _finding(
                    "warning",
                    rid,
                    "vague",
                    f'"{word}" promises without a number: say how it is measured.',
                )
            )
        criteria = r.get("criteria") or []
        if not criteria:
            findings.append(
                _finding(
                    "blocking",
                    rid,
                    "no_criteria",
                    "The requirement has no acceptance criterion; add at least one given / when / then.",
                )
            )
        for c in criteria:
            cid = c.get("id", f"{rid}.?")
            empty = [step for step in ("given", "when", "then") if not c.get(step)]
            if empty:
                findings.append(
                    _finding(
                        "blocking",
                        cid,
                        "empty_step",
                        f"The criterion leaves {', '.join(empty)} empty.",
                    )
                )
            elif word := _vague(
                " ".join(c.get(step, "") for step in ("given", "when", "then"))
            ):
                findings.append(
                    _finding(
                        "warning",
                        cid,
                        "vague",
                        f'"{word}" cannot be tested as written: say what is observed.',
                    )
                )
    quality = spec.get("quality") or []
    if not quality:
        findings.append(
            _finding(
                "warning",
                "spec",
                "no_quality",
                "No quality attribute (performance, availability, security…) with a number to measure it by.",
            )
        )
    for q in quality:
        qid = q.get("id", "NFR-?")
        if not q.get("attribute"):
            findings.append(
                _finding(
                    "blocking",
                    qid,
                    "empty_attribute",
                    "The quality attribute has no name.",
                )
            )
        if not _DIGIT.search(q.get("target") or ""):
            findings.append(
                _finding(
                    "blocking",
                    qid,
                    "no_number",
                    "The target carries no number; a quality attribute without one cannot be measured.",
                )
            )
    for index, q in enumerate(spec.get("questions") or [], 1):
        if not q.get("answer"):
            findings.append(
                _finding(
                    "blocking",
                    f"Q{index}",
                    "open_question",
                    f"Unanswered: {q.get('question', '')}",
                )
            )
    blocking = sum(1 for f in findings if f["severity"] == "blocking")
    return {
        "ok": blocking == 0,
        "blocking": blocking,
        "warnings": len(findings) - blocking,
        "findings": findings,
    }


def as_prompt(spec: dict[str, Any], criteria_ids: set[str] | None = None) -> str:
    """The specification as a prompt section; with *criteria_ids*, only those criteria.

    A requirement is included when at least one of its criteria is; the
    quality attributes and constraints hold for every task and are always
    included.
    """
    lines: list[str] = []
    for r in spec.get("requirements") or []:
        criteria = [
            c
            for c in r.get("criteria") or []
            if criteria_ids is None or c.get("id") in criteria_ids
        ]
        if not criteria:
            continue
        lines.append(f"### {r.get('id')}: {r.get('title')}")
        if r.get("description"):
            lines.append(r["description"])
        lines += [
            f"- {c['id']}: given {c.get('given')}, when {c.get('when')}, then {c.get('then')}"
            for c in criteria
        ]
        lines.append("")
    quality = spec.get("quality") or []
    if quality:
        lines.append("### Quality attributes (every task)")
        lines += [
            f"- {q['id']}: {q.get('attribute')} — {q.get('target')}" for q in quality
        ]
        lines.append("")
    constraints = spec.get("constraints") or []
    if constraints:
        lines.append("### Constraints (every task)")
        lines += [f"- {c}" for c in constraints]
    return "\n".join(lines).strip()
