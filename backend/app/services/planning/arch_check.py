"""The deterministic check of an architecture, its numbering and its renderings.

``normalize`` assigns the ids in order (``MOD-1``, ``IF-1``, ``ADR-1``) and
trims every text, so an id is never trusted from a draft; what a draft named
as a dependency, a consumer or a covered requirement is mapped from the
names or ids it used onto the assigned ids, and what cannot be mapped is a
finding. ``check`` lists what stops an approval: no module, a module without
a responsibility, a reference to a module or requirement that does not
exist, a cycle in the dependencies, a decision without its decision, a
question nobody answered; and what a person should look at: a requirement
no module covers, a module without a place in the repository, an interface
without a contract, no decision recorded.
"""

from __future__ import annotations

import re
from typing import Any

MAX_MODULES = 40
MAX_INTERFACES = 60
MAX_DECISIONS = 30
MAX_CONVENTIONS = 30
MAX_QUESTIONS = 20
TEXT_LIMIT = 1500
TITLE_LIMIT = 200
INTERFACE_KINDS = ("http", "function", "event", "cli", "schema", "other")


def _text(value: Any, limit: int = TEXT_LIMIT) -> str:
    return " ".join(str(value if value is not None else "").split())[:limit]


def _block(value: Any, limit: int = TEXT_LIMIT) -> str:
    """Multi-line text, kept as written (a contract keeps its lines)."""
    return str(value if value is not None else "").strip()[:limit]


def _items(value: Any, limit: int) -> list[Any]:
    return list(value)[:limit] if isinstance(value, list | tuple) else []


def _refs(value: Any) -> list[str]:
    if isinstance(value, str):
        value = [part for part in value.replace(";", ",").split(",")]
    return [_text(v, TITLE_LIMIT) for v in _items(value, 50) if _text(v, TITLE_LIMIT)]


def _decision_record(d: dict[str, Any]) -> dict[str, str]:
    """A decision's optional ADR fields, kept only when well formed: ``date``
    (``YYYY-MM-DD``, when it was accepted) and ``slug`` (the ADR file's name,
    lowercase words and hyphens). See ``services/planning/adr_docs.py``."""
    out: dict[str, str] = {}
    date = _text(d.get("date"), 10)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        out["date"] = date
    slug = _text(d.get("slug"), 60).lower()
    if re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", slug):
        out["slug"] = slug
    return out


def normalize(arch: dict[str, Any]) -> dict[str, Any]:
    """The architecture's content with ids assigned in order and texts trimmed.

    References between modules (``depends_on``, ``consumers``, an
    interface's ``module_id``) are mapped from the draft's own ids or module
    names onto the assigned ids; one that matches nothing is kept as written,
    for ``check`` to report. ``covers`` keeps the requirement ids as written
    (``REQ-3``; a criterion id is mapped to its requirement).
    """
    raw_modules = [
        m for m in _items(arch.get("modules"), MAX_MODULES) if isinstance(m, dict)
    ]
    assigned: dict[str, str] = {}
    for number, m in enumerate(raw_modules, 1):
        mid = f"MOD-{number}"
        for key in (m.get("id"), m.get("name")):
            if _text(key, TITLE_LIMIT):
                assigned.setdefault(_text(key, TITLE_LIMIT).casefold(), mid)

    def module_ref(value: str) -> str:
        return assigned.get(value.casefold(), value)

    def requirement_ref(value: str) -> str:
        head = value.upper().split(".")[0]
        return head if head.startswith("REQ-") else value

    modules = [
        {
            "id": f"MOD-{number}",
            "name": _text(m.get("name"), TITLE_LIMIT),
            "responsibility": _text(m.get("responsibility")),
            "path": _text(m.get("path"), TITLE_LIMIT),
            "depends_on": list(
                dict.fromkeys(module_ref(r) for r in _refs(m.get("depends_on")))
            ),
            "covers": list(
                dict.fromkeys(requirement_ref(r) for r in _refs(m.get("covers")))
            ),
        }
        for number, m in enumerate(raw_modules, 1)
    ]
    for m in modules:
        m["depends_on"] = [d for d in m["depends_on"] if d != m["id"]]
    interfaces = []
    for number, i in enumerate(_items(arch.get("interfaces"), MAX_INTERFACES), 1):
        if not isinstance(i, dict):
            continue
        kind = _text(i.get("kind"), 20).lower()
        interfaces.append(
            {
                "id": f"IF-{number}",
                "name": _text(i.get("name"), TITLE_LIMIT),
                "kind": kind if kind in INTERFACE_KINDS else "other",
                "module_id": module_ref(
                    _text(i.get("module_id") or i.get("module"), TITLE_LIMIT)
                ),
                "contract": _block(i.get("contract")),
                "consumers": list(
                    dict.fromkeys(module_ref(r) for r in _refs(i.get("consumers")))
                ),
            }
        )
    decisions = [
        {
            "id": f"ADR-{number}",
            "title": _text(d.get("title"), TITLE_LIMIT),
            "context": _text(d.get("context")),
            "decision": _text(d.get("decision")),
            "consequences": _text(d.get("consequences")),
            **_decision_record(d),
        }
        for number, d in enumerate(_items(arch.get("decisions"), MAX_DECISIONS), 1)
        if isinstance(d, dict)
    ]
    conventions = [
        _text(c) for c in _items(arch.get("conventions"), MAX_CONVENTIONS) if _text(c)
    ]
    questions = []
    for q in _items(arch.get("questions"), MAX_QUESTIONS):
        if isinstance(q, dict):
            question, answer = _text(q.get("question")), _text(q.get("answer"))
        else:
            question, answer = _text(q), ""
        if question:
            questions.append({"question": question, "answer": answer or None})
    return {
        "title": _text(arch.get("title"), TITLE_LIMIT),
        "overview": _text(arch.get("overview"), 4000),
        "modules": modules,
        "interfaces": interfaces,
        "decisions": decisions,
        "conventions": conventions,
        "questions": questions,
    }


def _finding(severity: str, where: str, code: str, message: str) -> dict[str, str]:
    return {"severity": severity, "where": where, "code": code, "message": message}


def find_cycle(modules: list[dict[str, Any]]) -> list[str] | None:
    """A cycle in ``depends_on`` as the ids along it, or ``None``."""
    graph = {m["id"]: [d for d in m.get("depends_on") or []] for m in modules}
    state: dict[str, int] = {}
    stack: list[str] = []

    def visit(node: str) -> list[str] | None:
        state[node] = 1
        stack.append(node)
        for dep in graph.get(node, []):
            if dep not in graph:
                continue
            if state.get(dep) == 1:
                return stack[stack.index(dep) :] + [dep]
            if state.get(dep) is None and (cycle := visit(dep)):
                return cycle
        stack.pop()
        state[node] = 2
        return None

    for mid in graph:
        if state.get(mid) is None and (cycle := visit(mid)):
            return cycle
    return None


def check(arch: dict[str, Any], requirement_ids: list[str]) -> dict[str, Any]:
    """What keeps a normalized architecture from approval, and what to look at.

    *requirement_ids* are the specification's (``REQ-1``…): a module may
    cover only those, and one no module covers is a warning.
    """
    findings: list[dict[str, str]] = []
    modules = arch.get("modules") or []
    known = {m["id"] for m in modules}
    requirements = set(requirement_ids)
    if not arch.get("title"):
        findings.append(
            _finding("blocking", "arch", "no_title", "The architecture has no title.")
        )
    if not arch.get("overview"):
        findings.append(
            _finding(
                "warning",
                "arch",
                "no_overview",
                "Say in a few sentences how the system is put together.",
            )
        )
    if not modules:
        findings.append(
            _finding(
                "blocking", "arch", "no_modules", "The architecture names no module."
            )
        )
    for m in modules:
        mid = m["id"]
        if not m.get("name"):
            findings.append(
                _finding("blocking", mid, "empty_name", "The module has no name.")
            )
        if not m.get("responsibility"):
            findings.append(
                _finding(
                    "blocking",
                    mid,
                    "no_responsibility",
                    "The module says nothing about what it is responsible for.",
                )
            )
        if not m.get("path"):
            findings.append(
                _finding(
                    "warning",
                    mid,
                    "no_path",
                    "The module has no place in the repository; name its folder or file.",
                )
            )
        for dep in m.get("depends_on") or []:
            if dep not in known:
                findings.append(
                    _finding(
                        "blocking",
                        mid,
                        "unknown_module",
                        f'It depends on "{dep}", which is no module here.',
                    )
                )
        for req in m.get("covers") or []:
            if req not in requirements:
                findings.append(
                    _finding(
                        "blocking",
                        mid,
                        "unknown_requirement",
                        f'It covers "{req}", which is not in the specification.',
                    )
                )
    if cycle := find_cycle(modules):
        findings.append(
            _finding(
                "blocking",
                cycle[0],
                "dependency_cycle",
                "The dependencies run in a circle: " + " → ".join(cycle) + ".",
            )
        )
    covered = {req for m in modules for req in m.get("covers") or []}
    for req in requirement_ids:
        if req not in covered:
            findings.append(
                _finding(
                    "warning",
                    req,
                    "uncovered_requirement",
                    "No module covers this requirement.",
                )
            )
    for i in arch.get("interfaces") or []:
        iid = i["id"]
        if not i.get("name"):
            findings.append(
                _finding("blocking", iid, "empty_name", "The interface has no name.")
            )
        if i.get("module_id") not in known:
            findings.append(
                _finding(
                    "blocking",
                    iid,
                    "unknown_module",
                    f'It belongs to "{i.get("module_id")}", which is no module here.',
                )
            )
        for consumer in i.get("consumers") or []:
            if consumer not in known:
                findings.append(
                    _finding(
                        "blocking",
                        iid,
                        "unknown_module",
                        f'"{consumer}" consumes it, but is no module here.',
                    )
                )
        if not i.get("contract"):
            findings.append(
                _finding(
                    "warning",
                    iid,
                    "no_contract",
                    "The interface has no contract: write the signature, the schema or the routes.",
                )
            )
    decisions = arch.get("decisions") or []
    if not decisions:
        findings.append(
            _finding(
                "warning",
                "arch",
                "no_decisions",
                "No decision is recorded; the ones a task would otherwise take alone belong here.",
            )
        )
    for d in decisions:
        if not d.get("title"):
            findings.append(
                _finding(
                    "blocking", d["id"], "empty_title", "The decision has no title."
                )
            )
        if not d.get("decision"):
            findings.append(
                _finding(
                    "blocking",
                    d["id"],
                    "no_decision",
                    "The decision says what was considered but not what was decided.",
                )
            )
    for index, q in enumerate(arch.get("questions") or [], 1):
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


# ---------------------------------------------------------------------------
# Renderings: a prompt section for a task, a Markdown document for the repository
# ---------------------------------------------------------------------------


def modules_for(
    arch: dict[str, Any], requirement_ids: set[str]
) -> list[dict[str, Any]]:
    """The modules covering any of *requirement_ids*, with those they depend on."""
    modules = arch.get("modules") or []
    by_id = {m["id"]: m for m in modules}
    chosen: dict[str, dict[str, Any]] = {}
    for m in modules:
        if requirement_ids & set(m.get("covers") or []):
            chosen[m["id"]] = m
            for dep in m.get("depends_on") or []:
                if dep in by_id:
                    chosen.setdefault(dep, by_id[dep])
    return [m for m in modules if m["id"] in chosen]


def as_prompt(arch: dict[str, Any], requirement_ids: set[str] | None = None) -> str:
    """The architecture as a prompt section; with *requirement_ids*, only their modules.

    The decisions and conventions hold for every task and are always included.
    """
    modules = (
        modules_for(arch, requirement_ids)
        if requirement_ids is not None
        else arch.get("modules") or []
    )
    names = {m["id"]: m["name"] for m in arch.get("modules") or []}
    chosen = {m["id"] for m in modules}
    lines: list[str] = []
    for m in modules:
        lines.append(
            f"### {m['id']}: {m['name']}"
            + (f" — `{m['path']}`" if m.get("path") else "")
        )
        lines.append(m.get("responsibility") or "")
        if m.get("depends_on"):
            lines.append(
                "Depends on: "
                + ", ".join(f"{d} ({names.get(d, d)})" for d in m["depends_on"])
            )
        if m.get("covers"):
            lines.append("Covers: " + ", ".join(m["covers"]))
        lines.append("")
    interfaces = [
        i
        for i in arch.get("interfaces") or []
        if requirement_ids is None
        or i.get("module_id") in chosen
        or chosen & set(i.get("consumers") or [])
    ]
    if interfaces:
        lines.append("### Interfaces")
        for i in interfaces:
            lines.append(
                f"- {i['id']}: {i['name']} ({i['kind']}, provided by {i['module_id']}"
                + (
                    ", used by " + ", ".join(i["consumers"])
                    if i.get("consumers")
                    else ""
                )
                + ")"
            )
            if i.get("contract"):
                lines.append("  " + i["contract"].replace("\n", "\n  "))
        lines.append("")
    decisions = arch.get("decisions") or []
    if decisions:
        lines.append("### Decisions (every task)")
        lines += [f"- {d['id']}: {d['title']} — {d.get('decision')}" for d in decisions]
        lines.append("")
    conventions = arch.get("conventions") or []
    if conventions:
        lines.append("### Conventions (every task)")
        lines += [f"- {c}" for c in conventions]
    return "\n".join(lines).strip()


def as_markdown(arch: dict[str, Any], spec_title: str, version: int) -> str:
    """The architecture as ``docs/ARCHITECTURE.md``: what the repository keeps."""
    names = {m["id"]: m["name"] for m in arch.get("modules") or []}
    lines = [
        f"# {arch.get('title') or 'Architecture'}",
        "",
        (
            f"Architecture v{version} for the specification *{spec_title}*. "
            "Written by La Fabrique from the approved architecture; edit it there, not here."
        ),
        "",
    ]
    if arch.get("overview"):
        lines += ["## Overview", "", arch["overview"], ""]
    lines += ["## Modules", ""]
    for m in arch.get("modules") or []:
        lines.append(f"### {m['id']}: {m['name']}")
        lines.append("")
        if m.get("path"):
            lines.append(f"- Where: `{m['path']}`")
        lines.append(f"- Responsibility: {m.get('responsibility') or ''}")
        if m.get("depends_on"):
            lines.append(
                "- Depends on: "
                + ", ".join(f"{d} ({names.get(d, d)})" for d in m["depends_on"])
            )
        if m.get("covers"):
            lines.append("- Covers: " + ", ".join(m["covers"]))
        lines.append("")
    if arch.get("interfaces"):
        lines += ["## Interfaces", ""]
        for i in arch["interfaces"]:
            lines.append(f"### {i['id']}: {i['name']}")
            lines.append("")
            lines.append(
                f"- Kind: {i['kind']}; provided by {i['module_id']} ({names.get(i['module_id'], '')})"
                + (
                    "; used by " + ", ".join(i["consumers"])
                    if i.get("consumers")
                    else ""
                )
            )
            if i.get("contract"):
                lines += ["", "```", i["contract"], "```"]
            lines.append("")
    if arch.get("decisions"):
        lines += ["## Decisions", ""]
        for d in arch["decisions"]:
            lines += [f"### {d['id']}: {d['title']}", ""]
            if d.get("context"):
                lines.append(f"**Context.** {d['context']}")
            lines.append(f"**Decision.** {d.get('decision') or ''}")
            if d.get("consequences"):
                lines.append(f"**Consequences.** {d['consequences']}")
            lines.append("")
    if arch.get("conventions"):
        lines += ["## Conventions", ""]
        lines += [f"- {c}" for c in arch["conventions"]]
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
