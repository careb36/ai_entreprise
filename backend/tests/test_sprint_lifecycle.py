"""Integration coverage for creating, executing, reviewing, and completing a sprint."""

import pytest
from sqlmodel import select

from models.company import Company
from models.sprint import BacklogItem, OutboxEvent, Sprint


class _StopAfterSubscribe(Exception):
    pass


class _RecordingNatsClient:
    def __init__(self):
        self.events = []

    async def subscribe(self, subject, callback):
        assert subject == "task.assign"
        self.task_callback = callback
        raise _StopAfterSubscribe

    async def publish(self, subject, payload):
        self.events.append((subject, payload))


@pytest.mark.asyncio
async def test_sprint_runs_from_start_through_task_execution_and_completion(
    sqlite_session, test_client, monkeypatch
):
    from services import task_worker

    company = Company(company_name="Lifecycle Test")
    sqlite_session.add(company)
    sqlite_session.commit()
    sqlite_session.refresh(company)

    started = test_client.post(
        "/v1/start_sprint",
        json={
            "company_id": company.id,
            "sprint_name": "Lifecycle Sprint",
            "start_date": "2026-10-17",
            "scope_size": "S",
            "goal": "Complete one task",
            "backlog_items": [
                {
                    "title": "Implement lifecycle coverage",
                    "description": "Exercise the sprint execution and review flow",
                    "assignee": "Developer",
                }
            ],
        },
    )

    assert started.status_code == 200, started.text
    sprint_id = started.json()["sprint_id"]
    item_id = started.json()["backlog_item_ids"][0]
    dispatch = sqlite_session.exec(
        select(OutboxEvent).where(
            OutboxEvent.aggregate_type == "backlog_item",
            OutboxEvent.aggregate_id == str(item_id),
        )
    ).one()
    assert dispatch.payload["subject"] == "task.assign"

    monkeypatch.setattr(task_worker, "engine", sqlite_session.get_bind())
    monkeypatch.setattr(task_worker, "AUTO_SELF_IMPROVEMENT_ENABLED", False)
    monkeypatch.setattr(task_worker, "_fetch_memory_context", lambda *_: "")
    monkeypatch.setattr(task_worker, "_load_sprint_completed_tasks", lambda *_: "")
    monkeypatch.setattr(
        task_worker,
        "_execute_task",
        lambda *_args, **_kwargs: "A useful implementation deliverable. " * 25,
    )
    monkeypatch.setattr(
        task_worker,
        "_call_llm_for_review",
        lambda *_args, **_kwargs: "APPROVED: The deliverable meets the task requirements.",
    )

    async def no_console_advice(*_args):
        return {"combined": "", "sources": []}

    monkeypatch.setattr(
        task_worker, "_collect_console_assistant_advice", no_console_advice
    )
    nats = _RecordingNatsClient()
    with pytest.raises(_StopAfterSubscribe):
        await task_worker.process_backlog_tasks(nats)

    await nats.task_callback(None, dispatch.payload["message"])
    after_execution = test_client.get(f"/v1/sprints/{sprint_id}/board")
    assert after_execution.json()["columns"]["in_review"][0]["id"] == item_id

    await task_worker._review_cycle(nats)

    board = test_client.get(f"/v1/sprints/{sprint_id}/board")
    sprint = sqlite_session.get(Sprint, sprint_id)
    item = sqlite_session.get(BacklogItem, item_id)
    event_types = [payload.get("semantic_type") for _, payload in nats.events]
    assert board.status_code == 200
    assert board.json()["columns"]["done"][0]["id"] == item_id
    assert item.status == "done"
    assert sprint.status == "completed"
    assert "task.review_handoff" in event_types
    assert "task.approved" in event_types
