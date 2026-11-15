"""The bench's readers of the run's Postgres (``scorecard.py collect``).

- ``reported_tokens``: the audit log's KPI turns, the tokens of those whose
  usage the provider reported, the model served per role, each task's turns;
  of one company, or of the whole run for the token budget.
- ``prompt_sections``: each task's latest task-worker run's prompt measure
  (``prompt_sections`` in the stored verification evidence, issue #443).
- ``difficulty_estimates``: each task's first ``estimated`` event (issue
  #319), the difficulty stored before its first attempt.

All read observations the backend stored; none estimates what is not there.
"""

from typing import Any


def reported_tokens(
    dsn: dict[str, str], company_id: int | None
) -> tuple[
    dict[str, int], dict[str, list[str]], dict[int, list[tuple[str | None, int]]]
]:
    """The company's KPI turns in the audit log: tokens of the reported ones,
    how many were and were not reported, the model served per role, and each
    task's turns (``(usage status, cost)``) for the split by difficulty.
    With *company_id* None, every company's: the whole run, for the token
    budget (``budget.py``), since the bench's database is the run's alone."""
    import psycopg2  # the backend's environment has it

    tokens = {"reported": 0, "turns_reported": 0, "turns_unreported": 0}
    models: dict[str, list[str]] = {}
    per_task: dict[int, list[tuple[str | None, int]]] = {}
    with psycopg2.connect(**dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT details->>'agent_role', details->>'model', details->>'tokens', cost_tokens, "
            "interaction_id FROM audit_log WHERE (%s IS NULL OR company_id = %s) "
            "AND details->>'tokens' IS NOT NULL",
            (company_id, company_id),
        )
        for role, model, status, cost, interaction in cur.fetchall():
            if status == "reported":
                tokens["reported"] += int(cost or 0)
                tokens["turns_reported"] += 1
            else:
                tokens["turns_unreported"] += 1
            if role and model:
                models.setdefault(role, []).append(model)
            prefix, _, number = (interaction or "").partition("-")
            if prefix == "task" and number.isdigit():
                per_task.setdefault(int(number), []).append((status, int(cost or 0)))
    return tokens, models, per_task


def prompt_sections(dsn: dict[str, str], company_id: int) -> dict[int, Any]:
    """Per task (``task-<id>``), the ``prompt_sections`` of its latest
    task-worker run: the newest verification of that run carrying one, else
    None. Tasks with no run are not listed: no run, no prompt measured."""
    import psycopg2  # the backend's environment has it

    out: dict[int, Any] = {}
    with psycopg2.connect(**dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT ON (r.external_ref) r.external_ref, "
            "v.evidence::jsonb -> 'prompt_sections' "
            "FROM agent_runs r LEFT JOIN verifications v ON v.agent_run_id = r.id "
            "WHERE r.company_id = %s AND r.external_ref LIKE 'task-%%' "
            "ORDER BY r.external_ref, r.version DESC, r.id DESC, "
            "(v.evidence::jsonb -> 'prompt_sections') IS NULL, v.id DESC",
            (company_id,),
        )
        for ref, measure in cur.fetchall():
            number = ref.partition("-")[2]
            if number.isdigit():
                out[int(number)] = measure
    return out


def difficulty_estimates(dsn: dict[str, str], company_id: int) -> dict[int, Any]:
    """Per task, the payload of its first ``estimated`` event (issue #319):
    the difficulty the task worker stored before its first attempt and the
    routing it led to. Tasks from before the event are not listed."""
    import psycopg2  # the backend's environment has it

    out: dict[int, Any] = {}
    with psycopg2.connect(**dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT ON (task_id) task_id, payload::jsonb FROM task_events "
            "WHERE company_id = %s AND event_type = 'estimated' AND task_id IS NOT NULL "
            "ORDER BY task_id, seq, id",
            (company_id,),
        )
        for task_id, payload in cur.fetchall():
            out[int(task_id)] = payload if isinstance(payload, dict) else {}
    return out
