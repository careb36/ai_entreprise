"""The task worker's side of a console producer (ADR 0012, 0016, issue #469 step 10).

A producer may not import the loop, so the loop lends a console producer what
it owns: ``TaskWorkerHost`` is ``producers.console_producer.ConsoleHost``
over the task worker (its engine, read from the worker when used, so a patch
of ``task_worker.engine`` still reaches the run; ``_load_procedures_context``,
``_update_item_status``, ``_resolve_workspace_root`` and ``_record_task_run``
from the worker's stages that own them, ``prompt``, ``board_writes``,
``deliver`` and ``record``, read as module attributes, so a patch of the stage
reaches it) and the loop's prompt sections (``carry_over``'s previous attempt,
``edit_plan``'s plan). ``produce`` is the call the worker makes.
"""

from __future__ import annotations

from typing import Any

from services.loop import carry_over, edit_plan
from services.loop.worker import board_writes, deliver, prompt, record
from services.producers import console_producer


def _worker() -> Any:
    """The task worker, imported when used (it imports this module)."""
    from services.loop import task_worker

    return task_worker


class TaskWorkerHost:
    """``ConsoleHost`` over the task worker."""

    def engine(self) -> Any:
        return _worker().engine

    def load_procedures_context(self, company_id: int, role: str | None) -> str:
        return prompt._load_procedures_context(company_id, role)

    def resolve_workspace_root(self, company_id: int) -> str | None:
        return deliver._resolve_workspace_root(company_id)

    def previous_attempt_context(self, item: dict[str, Any]) -> str:
        return carry_over.previous_attempt_context(item)

    def edit_plan_section(self, architect_advice: str | None) -> str:
        return edit_plan.prompt_section(architect_advice)

    def record_task_run(self, item, agent, sprint, text, files, evidence):
        return record._record_task_run(item, agent, sprint, text, files, evidence)

    def update_item_status(self, item_id: int, status: str, **kwargs: Any) -> Any:
        return board_writes._update_item_status(item_id, status, **kwargs)


HOST = TaskWorkerHost()


async def produce(
    nats_client: Any,
    item: dict[str, Any],
    agent: dict[str, Any],
    sprint: dict[str, Any],
    requirements_context: str | None = None,
    architect_advice: str | None = None,
) -> str | None:
    """``console_producer.produce`` for the task worker, with its host."""
    return await console_producer.produce(
        nats_client,
        item,
        agent,
        sprint,
        requirements_context,
        architect_advice,
        host=HOST,
    )
