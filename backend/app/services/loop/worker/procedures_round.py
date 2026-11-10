"""The procedures round after a sprint closes (ADR 0014, the task worker's close stage).

``_evaluate_procedures`` keeps or reverts the company's procedures that have
enough sprints since adoption, by the board's numbers, and tells the team;
``_propose_procedures`` asks the ScrumMaster (``_ask_scrum_master_for_procedures``)
for new ones after a sprint with rework or blocked tasks, adopted at once at
autonomy level 3 and otherwise left for a Manager. ``close._finalize_sprint_if_ready``
runs the round; it is a module of its own so the close stays under the size
limit. Moved out of ``task_worker``, which still re-exports these names.
"""

from typing import Any

from database import engine
from models import Agent, Company
from sqlmodel import Session, select

from services.learning import procedures, retrospective, scrum_master
from services.loop.worker import messages, prompt
from services.tasks.board import SCRUM_MASTER_ROLE


def _evaluate_procedures(company_id: int, sprint_id: int) -> None:
    """Keep or revert procedures with enough sprints since adoption, and tell the team."""
    with Session(engine) as session:
        decided = procedures.evaluate(session, company_id)
        for procedure in decided:
            session.add(
                messages._message(
                    company_id,
                    sprint_id,
                    None,
                    SCRUM_MASTER_ROLE,
                    SCRUM_MASTER_ROLE,
                    None,
                    "proposal",
                    procedures.describe_outcome(procedure),
                )
            )
        session.commit()


def _ask_scrum_master_for_procedures(
    company_id: int, sprint_id: int, retro_metrics: dict[str, Any]
) -> tuple[str, list[str]]:
    """The ScrumMaster's answer, and the company's roles it may propose for."""
    lessons = prompt._load_recent_lessons(company_id, 0)
    with Session(engine) as session:
        roles = sorted(
            {
                role
                for role in session.exec(
                    select(Agent.role).where(Agent.company_id == company_id)
                ).all()
                if role
            }
        )
        current = procedures.procedures_prompt(
            list(
                session.exec(
                    select(procedures.Procedure).where(
                        procedures.Procedure.company_id == company_id,
                        procedures.Procedure.status == "active",
                    )
                ).all()
            )
        )
        history = procedures.history_prompt(session, company_id)
        request = (
            "You are the ScrumMaster. A sprint just finished. Read how it went and decide "
            "whether a written procedure for one of the roles would have prevented its "
            "rework or blocked tasks.\n\n"
            f"## What happened\n{retrospective.summary(retro_metrics)}\n\n"
            f"## Lessons from the last sprints\n{lessons or '(none)'}\n"
            f"{current or chr(10) + '## Company Procedures' + chr(10) + '(none yet)'}"
            f"{history}\n\n"
            "A procedure is a short, concrete rule a role can follow on every task. Do not "
            "repeat an existing one, nor one that was reverted or rejected, unless you "
            "change what made it fail.\n\n" + procedures.proposal_format(roles)
        )
        model, system_prompt = scrum_master.profile(session, company_id)
    # Outside the session, usual retries: a transient error must not lose proposals.
    answer = scrum_master.ask_model(
        model, system_prompt, request, company_id=company_id
    )
    return answer, roles


def _propose_procedures(
    company_id: int, sprint_id: int, retro_metrics: dict[str, Any]
) -> None:
    """After a sprint with rework or blocked tasks, the ScrumMaster proposes procedures.

    With ``autonomy_level`` 3 a proposal is adopted at once; otherwise it waits
    for a Manager. Either way the board's numbers keep or revert it later.
    """
    if not (retro_metrics.get("rework_cycles") or retro_metrics.get("blocked_tasks")):
        return  # nothing went wrong: nothing to learn
    answer, roles = _ask_scrum_master_for_procedures(
        company_id, sprint_id, retro_metrics
    )
    proposals = procedures.parse_proposals(answer, roles)
    if not proposals:
        return
    with Session(engine) as session:
        company = session.get(Company, company_id)
        autonomous = bool(company and (company.autonomy_level or 0) >= 3)
        created = procedures.propose(
            session,
            company_id,
            proposals,
            SCRUM_MASTER_ROLE,
            source_sprint_id=sprint_id,
            skip_failed=True,
        )
        for procedure in created:
            if autonomous:
                procedures.adopt(session, procedure, "autonomy")
            state = (
                "Adopted (autonomy level 3)"
                if autonomous
                else "Proposed, waiting for a Manager"
            )
            session.add(
                messages._message(
                    company_id,
                    sprint_id,
                    None,
                    SCRUM_MASTER_ROLE,
                    SCRUM_MASTER_ROLE,
                    None if procedure.role == procedures.ALL_ROLES else procedure.role,
                    "proposal",
                    f'{state}: "{procedure.title}" (v{procedure.version})\n'
                    f"{procedure.body}\nWhy: {procedure.rationale or '-'}",
                )
            )
        session.commit()
