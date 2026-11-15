"""The security documents of a pack: the WSTG checklist and the security test report (issue #539).

- **WSTG checklist** (``wstg_checklist.md``): every test of the versioned
  OWASP WSTG catalogue, whether it applies to this project and why (the rule
  table of ``services/planning/wstg_plan.py``), how it is settled (``auto``,
  by the daemon's probe or a QA test; ``manual``, by a person) and its status
  (``project_docs.wstg_status``): observed-pass / observed-fail / not-run /
  needs-manual / not-applicable, each observed row citing its verification
  (run, sha256 of the run's evidence, tool) or a person's recorded review.
- **Security test report** (``security_report.md``): structured after the
  guide's chapter 5 (Reporting): introduction, executive summary, findings,
  appendices. The findings are the observed failures only; prose a model
  writes would sit in its own quoted claim block, never in a finding.

Both are derivatives of the guide's checklist and carry its licence, CC BY-SA
4.0, in a footer with the attribution (``licence`` on the document too); the
code stays Apache-2.0. The guide's test names and references are reproduced
as the catalogue holds them; every other fixed string is ours (``texts``).

Pure; no I/O beyond the cached catalogue.
"""

from __future__ import annotations

from typing import Any

from services.planning import wstg_plan
from services.planning.project_docs import texts, wstg_status
from services.planning.project_docs.control_docs import arch_ref, cell, date
from services.planning.project_docs.layout import PMBOK, build, claim_block, tr_of
from services.planning.project_docs.refs import Sources, ref
from services.planning.project_docs.sanitize import md_id, md_inline
from services.planning.project_docs.scope_docs import spec_ref
from services.platform import wstg_catalogue

LICENCE = "CC BY-SA 4.0"
INPUTS = wstg_status.INPUTS
_SEVERITY = {"refuted": "high", "finding": "medium", "fail": "medium"}


def footer(lang: str) -> str:
    """The licence and attribution every generated checklist and report ends with."""
    return texts.translator(lang)(
        "wstg.footer", commit=wstg_catalogue.commit(), licence=LICENCE
    )


def signal_name(signal: str, tr: Any) -> str:
    """A rule's signal in the pack's language; ``api+auth`` reads as both."""
    return " + ".join(tr(f"wstg.signal.{part}") for part in signal.split("+"))


def _finish(doc: dict[str, Any], lang: str) -> dict[str, Any]:
    doc["markdown"] = doc["markdown"].rstrip("\n") + "\n\n---\n\n" + footer(lang) + "\n"
    doc["licence"] = LICENCE
    return doc


def _catalogue_ref(lang: str) -> dict[str, Any]:
    return ref(
        "catalogue",
        wstg_catalogue.commit()[:7],
        sha256=wstg_catalogue.commit(),
        label=texts.translator(lang)("wstg.catalogue_label"),
    )


def _evidence(row: dict[str, Any], src: Sources, tr: Any) -> str:
    ev = row.get("evidence")
    if not ev:
        return "-"
    if ev["kind"] == "verification":
        mark = src.cite(
            ref(
                "verification",
                ev.get("verification_id"),
                sha256=ev.get("artifact_sha256"),
                label=tr("label_run", id=ev.get("run_id")),
            )
        )
        return tr(
            "wstg.evidence_verification",
            id=md_id(ev.get("verification_id")),
            run=md_id(ev.get("run_id")),
            tool=cell(ev.get("tool"), 80),
            mark=mark,
        )
    mark = src.cite(
        ref(
            "review",
            ev.get("review_id"),
            sha256=ev.get("record_sha256"),
            label=tr("wstg.review_label", role=ev.get("reviewer_role") or "-"),
        )
    )
    return tr(
        "wstg.evidence_review",
        id=md_id(ev.get("review_id")),
        date=date(ev.get("reviewed_on")),
        artifact=cell(ev.get("artifact"), 80),
        mark=mark,
    )


def _why(row: dict[str, Any], tr: Any) -> str:
    """Why a row applies or not, from its rules and signals, in the pack's language."""
    if row.get("claim"):
        return tr("wstg.proposed", role=md_inline(row.get("proposed_by"), 40))
    if row.get("applicable"):
        return "; ".join(f"{r}: {tr('wstg.rule.' + r)}" for r in row.get("rules") or [])
    would = row.get("would_need") or []
    if would:
        return tr(
            "wstg.na_reason",
            signals=", ".join(signal_name(s, tr) for s in would),
        )
    return tr("wstg.na_none")


def _scope_marks(snapshot: dict[str, Any], src: Sources, lang: str) -> str:
    marks = []
    if snapshot.get("specification"):
        marks.append(src.cite(spec_ref(snapshot["specification"], lang)))
    if snapshot.get("architecture"):
        marks.append(src.cite(arch_ref(snapshot["architecture"], lang)))
    stack = snapshot.get("stack")
    if stack:
        marks.append(
            src.cite(
                ref(
                    "discovery",
                    stack.get("discovery_id"),
                    sha256=stack.get("sha256"),
                    label=texts.translator(lang)("label_stack"),
                )
            )
        )
    return " ".join(marks)


def _signals(plan: dict[str, Any], tr: Any, marks: str) -> list[str]:
    found = plan.get("signals") or {}
    if not found:
        return [tr("wstg.no_signals", marks=marks or f"({tr('no_source')})")]
    lines = [tr("wstg.signals_header"), "|---|---|"]
    for signal, places in found.items():
        where = "; ".join(_place(p, tr) for p in places)
        lines.append(f"| {tr('wstg.signal.' + signal)} | {where} {marks} |")
    return lines


def _place(place: dict[str, Any], tr: Any) -> str:
    """Where a signal was found, in the pack's language; record text stays as recorded."""
    kind = place.get("kind")
    match = md_inline(place.get("match"), 60)
    if kind == "interface":
        return tr("wstg.where.interface", id=md_id(place.get("id")), kind=match)
    if kind in ("framework", "database"):
        return tr(f"wstg.where.{kind}", name=match)
    if kind == "implied":
        return tr("wstg.where.implied", signal=tr(f"wstg.signal.{place.get('match')}"))
    return tr(
        "wstg.where.text",
        record=tr(f"kind.{place.get('record')}"),
        id=md_id(place.get("id")),
        match=match,
    )


def checklist(snapshot: dict[str, Any]) -> dict[str, Any]:
    lang, tr = tr_of(snapshot)
    src = Sources(lang)
    plan = wstg_plan.plan(snapshot)
    rows = wstg_status.rows(snapshot, plan)
    count = wstg_status.counts(rows)
    marks = _scope_marks(snapshot, src, lang)
    body = [
        tr("wstg.framework", commit=wstg_catalogue.commit()[:7])
        + " "
        + src.cite(_catalogue_ref(lang)),
        "",
        tr("wstg.checklist_intro"),
        "",
        tr("wstg.signals_heading"),
        "",
        *_signals(plan, tr, marks),
        "",
        tr("wstg.counts", **{k.replace("-", "_"): v for k, v in count.items()}),
        "",
        tr("wstg.applicable_heading"),
        "",
        tr("wstg.table_header"),
        "|---|---|---|---|---|---|",
    ]
    shown = [r for r in rows if r["applicable"] or r["evidence"]]
    for r in shown:
        how = (
            f"{tr('wstg.mode.' + r['mode'])} ({tr('wstg.by.' + r['observed_by'])})"
            if r.get("mode")
            else "-"
        )
        status = tr(f"wstg.status.{r['status']}")
        if r["unverifiable"] and not r["evidence"]:
            status += " " + tr("wstg.unverifiable", n=len(r["unverifiable"]))
        body.append(
            f"| {md_id(r['id'])} | {md_inline(r['name'], 90)} | {how} | "
            f"{md_inline(_why(r, tr), 240)} | {status} | {_evidence(r, src, tr)} |"
        )
    if not shown:
        body.append(f"| - | {tr('wstg.none_applicable')} | - | - | - | - |")
    body += [
        "",
        tr("wstg.not_applicable_heading"),
        "",
        tr("wstg.na_header"),
        "|---|---|---|",
    ]
    shown_ids = {r["id"] for r in shown}
    for r in rows:
        if r["id"] in shown_ids:
            continue
        body.append(
            f"| {md_id(r['id'])} | {md_inline(r['name'], 90)} | {md_inline(_why(r, tr), 200)} |"
        )
    missing = []
    if not snapshot.get("specification") and not snapshot.get("architecture"):
        missing.append(tr("wstg.missing_scope"))
    persons = [tr("wstg.persons_manual")] if count[wstg_status.NEEDS_MANUAL] else []
    claims = [
        {"section": tr("wstg.proposed_section"), "role": r.get("proposed_by")}
        for r in rows
        if r.get("claim")
    ]
    doc = build(
        "wstg_checklist", PMBOK, body, src, snapshot, INPUTS, missing, claims, persons
    )
    return _finish(doc, lang)


def _findings(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in rows if r["status"] == wstg_status.OBSERVED_FAIL]


def _severity(row: dict[str, Any], tr: Any) -> str:
    ev = row.get("evidence") or {}
    if ev.get("kind") == "review":
        return tr("wstg.severity.person")
    return tr(f"wstg.severity.{_SEVERITY.get(str(ev.get('item_status')), 'medium')}")


def _dates(snapshot: dict[str, Any]) -> list[str]:
    stamps = [
        str(o.get("observed_at"))[:10]
        for o in snapshot.get("wstg_observations") or []
        if o.get("observed_at")
    ] + [
        str(r.get("created_at"))[:10]
        for r in snapshot.get("wstg_reviews") or []
        if r.get("created_at")
    ]
    return sorted(stamps)


def report(snapshot: dict[str, Any], prose: str | None = None) -> dict[str, Any]:
    """The security test report, after the WSTG's chapter 5 (Reporting)."""
    lang, tr = tr_of(snapshot)
    src = Sources(lang)
    plan = wstg_plan.plan(snapshot)
    rows = wstg_status.rows(snapshot, plan)
    count = wstg_status.counts(rows)
    findings = _findings(rows)
    marks = _scope_marks(snapshot, src, lang)
    applicable = [r for r in rows if r["applicable"]]
    by = {
        k: sum(1 for r in applicable if r.get("observed_by") == k)
        for k in ("security_probe", "qa_test", "person")
    }
    dates = _dates(snapshot)
    body = [
        tr("wstg.report_framework") + " " + src.cite(_catalogue_ref(lang)),
        "",
        tr("wstg.r.intro"),
        "",
        tr("wstg.r.version"),
        "",
        tr("wstg.r.version_body", commit=wstg_catalogue.commit()[:7]),
        "",
        tr("wstg.r.team"),
        "",
        tr(
            "wstg.r.team_body",
            probe=by["security_probe"],
            qa=by["qa_test"],
            person=by["person"],
        ),
        "",
        tr("wstg.r.scope"),
        "",
        tr(
            "wstg.r.scope_body",
            applicable=len(applicable),
            total=len(rows),
            marks=marks or f"({tr('no_source')})",
        ),
        "",
        tr("wstg.r.limitations"),
        "",
        tr(
            "wstg.r.limitations_body",
            not_run=count[wstg_status.NOT_RUN],
            manual=count[wstg_status.NEEDS_MANUAL],
            unverifiable=sum(
                1 for r in rows if r["unverifiable"] and not r["evidence"]
            ),
        ),
        "",
        tr("wstg.r.timeline"),
        "",
        tr("wstg.r.timeline_body", first=date(dates[0]), last=date(dates[-1]))
        if dates
        else tr("missing_line", what=tr("wstg.r.no_timeline")),
        "",
        tr("wstg.r.disclaimer"),
        "",
        tr("wstg.r.disclaimer_body"),
        "",
        tr("wstg.r.summary"),
        "",
        tr("wstg.counts", **{k.replace("-", "_"): v for k, v in count.items()}),
        "",
    ]
    prose_lines, has_prose = claim_block(
        lang, "TechWriter", prose, tr("wstg.r.prose_what")
    )
    body += [
        *prose_lines,
        "",
        tr("wstg.r.findings"),
        "",
        tr("wstg.r.findings_summary"),
        "",
    ]
    if findings:
        body += [tr("wstg.r.findings_header"), "|---|---|---|---|---|"]
        for n, r in enumerate(findings, 1):
            body.append(
                f"| F-{n} | {md_id(r['id'])} | {md_inline(r['name'], 90)} | "
                f"{_severity(r, tr)} | {_evidence(r, src, tr)} |"
            )
    else:
        body.append(tr("wstg.r.no_findings"))
    body += ["", tr("wstg.r.findings_details"), ""]
    for n, r in enumerate(findings, 1):
        ev = r.get("evidence") or {}
        test = wstg_catalogue.by_short(r["short"]) or {}
        observed = (
            ev.get("observation")
            if ev.get("kind") == "verification"
            else ev.get("note")
        )
        body += [
            f"#### F-{n}: {md_id(r['id'])} {md_inline(r['name'], 90)}",
            "",
            tr(
                "wstg.r.detail_observed",
                text=cell(observed, 240),
                evidence=_evidence(r, src, tr),
            ),
            tr("wstg.r.detail_reference", reference=cell(test.get("reference"), 200)),
            tr("wstg.r.detail_remediation"),
            "",
        ]
    body += [
        tr("wstg.r.appendices"),
        "",
        tr("wstg.r.methodology"),
        "",
        tr("wstg.r.methodology_body"),
        "",
    ]
    body += [
        f"- {rule}: {tr('wstg.rule.' + rule)} ({signal_name(signal, tr)})"
        for rule, signal, _reason, _tests in wstg_plan.RULES
    ]
    body += [
        "",
        tr("wstg.r.severity"),
        "",
        tr("wstg.r.severity_body"),
        "",
        tr("wstg.r.tools"),
        "",
    ]
    tools = sorted(
        {
            str(o.get("tool"))
            for o in snapshot.get("wstg_observations") or []
            if o.get("tool")
        }
    )
    body += [f"- {md_inline(t, 160)}" for t in tools] or [
        tr("missing_line", what=tr("wstg.r.no_tools"))
    ]
    missing = (
        []
        if findings or count[wstg_status.OBSERVED_PASS]
        else [tr("wstg.r.no_observation")]
    )
    claims = (
        [{"section": tr("wstg.r.prose_what"), "role": "TechWriter"}]
        if has_prose
        else []
    )
    persons = [tr("wstg.r.remediation_person")] if findings else []
    doc = build(
        "security_report", PMBOK, body, src, snapshot, INPUTS, missing, claims, persons
    )
    return _finish(doc, lang)
