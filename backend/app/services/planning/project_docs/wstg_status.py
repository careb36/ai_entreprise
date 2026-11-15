"""The status of each row of a project's WSTG checklist, from observations and reviews only.

The plan (``services/planning/wstg_plan.py``) says which tests apply and how
each can be settled. Its status here comes from exactly two kinds of record
(issue #539):

- an **observation**: a WSTG item of a Verifier's check (the security probe,
  ``evidence/security_probe.py``; a test named after the WSTG test,
  ``evidence/wstg_tests.py``), cited with the verification id, the run, the
  sha256 of the run's evidence and the tool;
- a **person's recorded review** (``WstgReview``): the review's id, date,
  artifact, the recorder's company role and the digest the backend computed.

The newest of them sets the row. An item the check could not observe
(``unverifiable``) sets nothing: it is listed beside the row. Without either,
an applicable ``auto`` row is ``not-run`` and a ``manual`` one
``needs-manual``; a row the plan did not select is ``not-applicable`` with the
plan's reason. A row is never ``observed-*`` without a verification id or a
review id (a test holds it). Nothing a producer reported about its own run is
read.

Pure; no I/O.
"""

from __future__ import annotations

from typing import Any

from services.planning import wstg_plan

OBSERVED_PASS = "observed-pass"
OBSERVED_FAIL = "observed-fail"
NOT_RUN = "not-run"
NEEDS_MANUAL = "needs-manual"
NOT_APPLICABLE = "not-applicable"
STATUSES = (OBSERVED_PASS, OBSERVED_FAIL, NOT_RUN, NEEDS_MANUAL, NOT_APPLICABLE)
# The inputs a checklist reads, for the pack's drift digest.
INPUTS = ("specification", "architecture", "stack", "wstg_observations", "wstg_reviews")

_ITEM_STATUS = {
    "pass": OBSERVED_PASS,
    "refuted": OBSERVED_FAIL,
    "finding": OBSERVED_FAIL,
    "fail": OBSERVED_FAIL,
}
_REVIEW_STATUS = {
    "pass": OBSERVED_PASS,
    "fail": OBSERVED_FAIL,
    "not_applicable": NOT_APPLICABLE,
}


def _events(snapshot: dict[str, Any]) -> tuple[dict[str, list], dict[str, list]]:
    """Per versioned id, the dated records that may set its status, and the
    observations that could not (``unverifiable``)."""
    setting: dict[str, list[tuple[str, int, dict[str, Any]]]] = {}
    unverifiable: dict[str, list[dict[str, Any]]] = {}
    for obs in snapshot.get("wstg_observations") or []:
        for item in obs.get("items") or []:
            base = {
                "kind": "verification",
                "verification_id": obs.get("verification_id"),
                "run_id": obs.get("run_id"),
                "artifact_sha256": obs.get("artifact_sha256"),
                "tool": obs.get("tool"),
                "check": obs.get("check"),
                "observed_at": obs.get("observed_at"),
                "observation": item.get("observation"),
                "item_status": item.get("status"),
            }
            status = _ITEM_STATUS.get(str(item.get("status")))
            if status is None or not obs.get("verification_id"):
                unverifiable.setdefault(item["id"], []).append(base)
                continue
            setting.setdefault(item["id"], []).append(
                (str(obs.get("observed_at") or ""), 0, {**base, "status": status})
            )
    for review in snapshot.get("wstg_reviews") or []:
        status = _REVIEW_STATUS.get(str(review.get("result")))
        if status is None or not review.get("id"):
            continue
        setting.setdefault(str(review.get("wstg_id")), []).append(
            (
                str(review.get("created_at") or ""),
                1,
                {
                    "kind": "review",
                    "status": status,
                    "review_id": review.get("id"),
                    "reviewed_on": review.get("reviewed_on"),
                    "artifact": review.get("artifact"),
                    "note": review.get("note"),
                    "reviewer_role": review.get("reviewer_role"),
                    "record_sha256": review.get("record_sha256"),
                },
            )
        )
    return setting, unverifiable


def rows(
    snapshot: dict[str, Any], plan: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    """Every catalogue test with its plan row, its status and what set it."""
    plan = plan or wstg_plan.plan(snapshot)
    setting, unverifiable = _events(snapshot)
    out = []
    for row in plan["rows"]:
        records = sorted(setting.get(row["id"], []), key=lambda r: (r[0], r[1]))
        evidence = records[-1][2] if records else None
        if evidence is not None:
            status = evidence["status"]
        elif not row.get("applicable"):
            status = NOT_APPLICABLE
        elif row.get("mode") == wstg_plan.AUTO:
            status = NOT_RUN
        else:
            status = NEEDS_MANUAL
        out.append(
            {
                **row,
                "status": status,
                "evidence": evidence,
                "unverifiable": unverifiable.get(row["id"], [])[-3:],
            }
        )
    return out


def counts(checklist: list[dict[str, Any]]) -> dict[str, int]:
    out = dict.fromkeys(STATUSES, 0)
    for row in checklist:
        out[row["status"]] += 1
    return out
