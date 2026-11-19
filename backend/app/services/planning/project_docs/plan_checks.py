"""The check results the quality and communications plans add (issue #530, phase 2).

Three checks, appended to ``checks.CHECKS`` with the same signature
(``(snapshot, sources, translator) -> (result, observed, marks)``):

- ``verified`` (Scope, measured): the Verifier's readings with the verdict
  verified over every reading; the share a project accepts is the Owner's.
- ``quality_targets`` (Scope, met or not met): the documented specifications
  name a target for every quality area the specification check expects
  (STD-PO-1, ``standards.missing_quality_areas``): a factory standard, so the
  records answer it.
- ``channels`` (Stakeholders, measured): the channels outside the product
  that are on (the notification webhook, Slack, Teams, the brief by mail),
  read from their configuration records; which ones a company uses is its
  choice.

The result names are ``checks``' own (``tests/test_project_docs_plans.py``
holds them equal); they are repeated here so ``checks`` can import this
module. Pure; no I/O.
"""

from __future__ import annotations

from typing import Any

from services.contracts.verdicts import VERDICT_VERIFIED
from services.planning.project_docs import comms_docs, quality_docs, specs
from services.planning.project_docs.log_docs import verification_ref
from services.planning.project_docs.refs import Sources, ref
from services.planning.project_docs.scope_docs import spec_ref

MET, NOT_MET, MEASURED, NOT_COMPUTABLE = "met", "not met", "measured", "not computable"
MAX_MARKS = 5
INPUTS = (quality_docs.BASIS, comms_docs.KEY)


def _cite(src: Sources, refs: list[dict[str, Any]]) -> str:
    if not refs:
        return ""
    more = f" (+{len(refs) - MAX_MARKS})" if len(refs) > MAX_MARKS else ""
    return src.cite(*refs[:MAX_MARKS]) + more


def verified(snapshot: dict, src: Sources, tr) -> tuple[str, str, str]:
    readings = snapshot.get("verifications") or []
    if not readings:
        return NOT_COMPUTABLE, tr("check.no_verified"), ""
    passed = [v for v in readings if v.get("verdict") == VERDICT_VERIFIED]
    marks = _cite(src, [verification_ref(v, src.lang) for v in readings])
    return (
        MEASURED,
        tr("check.verified_obs", part=len(passed), whole=len(readings)),
        marks,
    )


def quality_targets(snapshot: dict, src: Sources, tr) -> tuple[str, str, str]:
    listed = specs.documented(snapshot)
    if not listed:
        return NOT_COMPUTABLE, tr("check.no_specification"), ""
    gaps: dict[str, None] = {}
    for spec in listed:
        for area in quality_docs.missing_areas(spec):
            gaps.setdefault(area, None)
    marks = _cite(src, [spec_ref(s, src.lang) for s in listed])
    if not gaps:
        return MET, tr("check.quality_targets_yes"), marks
    observed = tr(
        "check.quality_targets_no", areas=quality_docs.area_names(list(gaps), tr)
    )
    return NOT_MET, observed, marks


def channels(snapshot: dict, src: Sources, tr) -> tuple[str, str, str]:
    found = comms_docs.config(snapshot)
    if found is None:
        return NOT_COMPUTABLE, tr("check.no_channels"), ""
    on = comms_docs.outside_on(snapshot)
    records = [("webhook", found.get("webhook"))]
    records += [("chat", row) for row in found.get("chat") or []]
    records += [("brief", found.get("brief"))]
    refs = [
        ref(kind, r.get("id") or r.get("policy_id") or kind, sha256=r.get("sha256"))
        for kind, r in records
        if isinstance(r, dict)
    ]
    observed = tr(
        "check.channels_obs", part=sum(on.values()), whole=len(comms_docs.OUTSIDE)
    )
    return MEASURED, observed, _cite(src, refs)
