"""The quality management plan of a pack (issue #530, phase 2).

How quality is defined, built in and checked on the project, from what the
backend itself applies (``quality_basis``, read by ``collect_plans``): the
standards catalogue with the roles each binds and whether it is checked
without a model, the quality targets of the specification and the areas left
without one (STD-PO-1, ``standards.missing_quality_areas``), the checks whose
refutation stops a delivery at the gate before QA, the risk tier the
backend's classifier gives each area, and the quality outcomes measured so
far (the status report's figures, ``performance``). The structure follows the
PMBOK Guide, 8th edition (PMI, 2025), where quality is planned within the
Scope performance domain's Planning focus area; the wording is our own, in
the pack's language (``texts``, ``texts_plans_*``).

Every item cites its record: a standard, a gate check or a risk tier with the
digest the backend computed over its definition, a target with its
specification's digest, an outcome with the verifications, effort
observations (chain hashes) or holds behind it. The plan reads no
retrospective or agent message. The objectives and acceptance thresholds the
project commits to are a person's section (``statements``): the factory
reports the figures and never sets the threshold.

Pure; no I/O.
"""

from __future__ import annotations

import re
from typing import Any

from services.evidence import standards
from services.planning.project_docs import performance, specs, statements, texts
from services.planning.project_docs.control_docs import cell, event_ref
from services.planning.project_docs.layout import PMBOK, build, missing_line, tr_of
from services.planning.project_docs.log_docs import verification_ref
from services.planning.project_docs.refs import Sources, ref
from services.planning.project_docs.sanitize import md_id, md_inline
from services.planning.project_docs.scope_docs import spec_ref
from services.planning.project_docs.status_docs import effort_ref

MAX_MARKS = 5
OBJECTIVES = "quality_plan.objectives"
BASIS = "quality_basis"
BASE_INPUTS = (
    "specification",
    BASIS,
    *performance.PERFORMANCE_INPUTS,
    statements.key_of("quality_plan"),
)
_CODE_DROP = re.compile(r"[^A-Za-z0-9._/\- ]")


def code(value: Any) -> str:
    """A check name or a path as a code span that cannot leave its backticks."""
    text = _CODE_DROP.sub("", str(value or ""))[:80]
    return f"`{text}`" if text else "-"


def _marks(src: Sources, refs: list[dict[str, Any]]) -> str:
    if not refs:
        return "-"
    out = src.cite(*refs[:MAX_MARKS])
    return out + (f" (+{len(refs) - MAX_MARKS})" if len(refs) > MAX_MARKS else "")


def missing_areas(spec: dict[str, Any]) -> list[str]:
    """The quality areas *spec* names no target for, as the specification check reads it."""
    return standards.missing_quality_areas(spec)


def area_names(areas: list[str], tr) -> str:
    return ", ".join(
        tr(f"quality.area.{a}")
        if f"quality.area.{a}" in texts.TEXTS["en"]
        else md_inline(a, 40)
        for a in areas
    )


def standard_ref(entry: dict[str, Any]) -> dict[str, Any]:
    return ref(
        "standard",
        entry.get("id"),
        sha256=entry.get("sha256"),
        label=entry.get("area") or "",
    )


def _standards(basis, src, missing, lang, tr) -> list[str]:
    found = (basis or {}).get("standards") or []
    body = [tr("quality.standards_heading"), ""]
    if not found:
        missing.append(tr("quality.section_standards"))
        return body + [missing_line(lang, tr("quality.no_basis"))]
    body += [tr("quality.standards_note"), "", tr("quality.standards_header")]
    body.append("|---|---|---|---|---|---|")
    for s in found:
        roles = s.get("roles") or []
        binds = (
            tr("quality.every_role")
            if standards.ALL in roles
            else ", ".join(md_inline(r, 40) for r in roles)
        )
        how = s.get("checked")
        checked = (
            tr("quality.checked_yes", how=md_inline(how))
            if how
            else tr("quality.checked_no")
        )
        body.append(
            f"| {md_id(s.get('id'))} | {cell(s.get('area'), 40)} | {binds or '-'} | "
            f"{cell(s.get('title'))} | {checked} | {src.cite(standard_ref(s))} |"
        )
    count = sum(1 for s in found if s.get("checked"))
    return body + ["", tr("quality.standards_count", checked=count, total=len(found))]


def _targets_of(spec, src, lang, tr) -> list[str]:
    mark = src.cite(spec_ref(spec, lang))
    rows = [q for q in spec.get("quality") or [] if isinstance(q, dict)]
    body = []
    if rows:
        body += [tr("quality.targets_header"), "|---|---|---|---|"]
        for q in rows:
            body.append(
                f"| {md_id(q.get('id'))} | {cell(q.get('attribute'), 80)} | "
                f"{cell(q.get('target'))} | {mark} |"
            )
    else:
        body.append(f"{tr('quality.no_targets')} {mark}")
    gaps = missing_areas(spec)
    body += [
        "",
        tr("quality.areas_missing", areas=area_names(gaps, tr))
        if gaps
        else tr("quality.areas_complete"),
    ]
    return body


def _targets(snapshot, src, missing, lang, tr) -> list[str]:
    listed = specs.documented(snapshot)
    body = [tr("quality.targets_heading"), ""]
    if not listed:
        missing.append(tr("quality.section_targets"))
        return body + [missing_line(lang, tr("no_spec"))]
    body += [tr("quality.targets_note"), ""]
    if len(listed) == 1:
        return body + _targets_of(listed[0], src, lang, tr)
    body += specs.intro(snapshot, src)
    for spec in listed:
        body += [specs.heading(spec, src.cite(spec_ref(spec, lang)), lang, 3), ""]
        body += [*_targets_of(spec, src, lang, tr), ""]
    return body[:-1]


def _gate(basis, src, missing, lang, tr) -> list[str]:
    found = (basis or {}).get("gate") or []
    body = [tr("quality.gate_heading"), ""]
    if not found:
        missing.append(tr("quality.section_gate"))
        return body + [missing_line(lang, tr("quality.no_basis"))]
    body += [
        tr("quality.gate_note"),
        "",
        tr("quality.gate_header"),
        "|---|---|---|---|",
    ]
    for i, g in enumerate(found, 1):
        fixed = f"quality.fixed_in.{g.get('fixed_in')}"
        asked = tr(fixed) if fixed in texts.TEXTS["en"] else "-"
        mark = src.cite(
            ref(
                "gate",
                f"G{i}",
                sha256=g.get("sha256"),
                label=f"{g.get('check')}: {g.get('problem')}",
            )
        )
        body.append(
            f"| {code(g.get('check'))} | {cell(g.get('problem'), 80)} | {asked} | {mark} |"
        )
    return body


def _risk(basis, src, missing, lang, tr) -> list[str]:
    found = (basis or {}).get("risk") or []
    body = [tr("quality.risk_heading"), ""]
    if not found:
        missing.append(tr("quality.section_risk"))
        return body + [missing_line(lang, tr("quality.no_basis"))]
    body += [
        tr("quality.risk_note"),
        "",
        tr("quality.risk_header"),
        "|---|---|---|---|",
    ]
    for i, r in enumerate(found, 1):
        mark = src.cite(
            ref(
                "risk_rule",
                f"R{i}",
                sha256=r.get("sha256"),
                label=f"{r.get('probe')}: {r.get('tier')}",
            )
        )
        area = cell(r.get("area"), 60) if r.get("area") else tr("quality.no_area")
        body.append(
            f"| {area} | {cell(texts.status(lang, r.get('tier')), 20)} | {code(r.get('probe'))} | {mark} |"
        )
    return body


def _outcomes(snapshot, src, missing, lang, tr) -> list[str]:
    body = [tr("quality.outcomes_heading"), ""]
    readings = snapshot.get("verifications") or []
    efforts = list(performance.effort_of(snapshot).values())
    if not readings and not efforts:
        missing.append(tr("quality.section_outcomes"))
        return body + [missing_line(lang, tr("quality.no_outcomes"))]
    total = performance.totals(snapshot)
    read = total["readings"]
    verification_marks = _marks(src, [verification_ref(v, lang) for v in readings])
    effort_marks = _marks(src, [effort_ref(e, lang) for e in efforts])
    high = [v for v in readings if v.get("tier") == "high"]
    holds = [e for e in snapshot.get("events") or [] if e.get("kind") == "risk_hold"]
    measures = [
        (
            "m_readings",
            tr(
                "quality.readings_value",
                verified=read["verified"],
                refuted=read["refuted"],
                unverifiable=read["unverifiable"],
            ),
            verification_marks,
        ),
        (
            "m_first_pass",
            tr("quality.of", part=total["first_pass"], whole=total["done_observed"]),
            effort_marks,
        ),
        ("m_rework", str(total["rework_cycles"]), effort_marks),
        ("m_send_backs", str(total["send_backs"]), effort_marks),
        ("m_review_failures", str(total["review_failures"]), effort_marks),
        (
            "m_high_risk",
            str(total["high_risk"]),
            _marks(src, [verification_ref(v, lang) for v in high]),
        ),
        (
            "m_risk_holds",
            str(total["risk_holds"]),
            _marks(src, [event_ref(e, lang) for e in holds]),
        ),
    ]
    body += [
        tr("quality.outcomes_note"),
        "",
        tr("quality.outcomes_header"),
        "|---|---|---|---|",
    ]
    for key, value, marks in measures:
        body.append(
            f"| {tr('quality.' + key)} | {value} | {tr('quality.' + key + '_how')} | {marks} |"
        )
    return body


def plan(snapshot: dict[str, Any]) -> dict[str, Any]:
    """The quality management plan from *snapshot*."""
    lang, tr = tr_of(snapshot)
    src, missing = Sources(lang), []
    basis = snapshot.get(BASIS)
    body = [tr("quality.intro"), ""]
    body += _standards(basis, src, missing, lang, tr)
    body += [""] + _targets(snapshot, src, missing, lang, tr)
    body += [""] + _gate(basis, src, missing, lang, tr)
    body += [""] + _risk(basis, src, missing, lang, tr)
    body += [""] + _outcomes(snapshot, src, missing, lang, tr)
    body += ["", f"## {tr('person.quality_objectives')}", ""]
    body += statements.lines(snapshot, OBJECTIVES, src)
    body += ["", tr("quality.read_heading"), "", tr("quality.read")]
    return build(
        "quality_plan",
        PMBOK,
        body,
        src,
        snapshot,
        specs.inputs(snapshot, BASE_INPUTS),
        missing,
        persons=[tr("person.quality_objectives")],
        statements=statements.filled(snapshot, "quality_plan"),
    )
