"""Whether an approved delivery waits for a person (the task worker's review stage).

QA's approval closes a task unless ``person_hold`` says a person decides:
a high risk tier (``services/evidence/risk.py``) or a protected path the
company's setting matches, the company's autonomy level
(``sprint_control.person_acceptance_reason``), or a delivery that wrote no
file, whose approval is the reviewer's word alone with nothing the Verifier
could check.
"""

import asyncio
from typing import Any

from database import engine
from models import BacklogItem, Company, Sprint
from sqlmodel import Session

from services.contracts.manifest import parse_file_manifest
from services.evidence import risk
from services.loop import sprint_control
from services.loop.worker import record
from services.platform import protected_paths_setting

NO_FILES_REASON = "the delivery wrote no file, so nothing was verified"


async def person_hold(
    session: Session,
    item: BacklogItem,
    sprint: Sprint,
    deliverable: str,
    needs_work: bool,
) -> tuple[bool, dict[str, Any] | None]:
    """Whether an approved delivery waits for a person, and the check that says why."""
    # Approved, but a high-risk delivery waits for a person at
    # every autonomy level (services/risk.py).
    risk_check = (
        None
        if needs_work
        else await asyncio.to_thread(record._delivery_risk, sprint.company_id, item.id)
    )
    if risk_check is None and not needs_work:
        # No recorded check (the run was not recorded): unknown is
        # not low, so the paths the deliverable declares decide; a
        # claim, which can only raise the tier.
        risk_check = risk.classify(
            [f["path"] for f in parse_file_manifest(deliverable)]
        )
    if not needs_work and protected_paths_setting.enabled(session, sprint.company_id):
        # The company hosts the factory under the setting as it is now, which
        # may postdate the verification: the recorded run's paths and the
        # deliverable's are matched again (record._protected_reasons).
        grader = await asyncio.to_thread(
            record._protected_reasons, sprint.company_id, item.id, deliverable
        )
        if grader:
            earlier = (risk_check or {}).get("reasons") or []
            risk_check = {"tier": risk.RISK_HIGH, "reasons": [*grader, *earlier][:10]}
    risk_hold = risk_check is not None and risk.is_high(risk_check.get("tier"))
    company = session.get(Company, sprint.company_id)
    person_reason = sprint_control.person_acceptance_reason(
        company.autonomy_level, engine, sprint.company_id, item.id
    )
    if not needs_work and not risk_hold and person_reason:
        risk_hold = True
        risk_check = {"reasons": [person_reason]}
    if not needs_work and not risk_hold:
        # An approval of a delivery that wrote no file is the reviewer's word
        # alone, with nothing the Verifier could check: a person decides.
        files = await asyncio.to_thread(
            record._delivered_files, sprint.company_id, item.id, deliverable
        )
        if not files:
            risk_hold = True
            risk_check = {"reasons": [NO_FILES_REASON]}
    return risk_hold, risk_check
