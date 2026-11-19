"""
A company's run window: work that can wait starts in the provider's off-peak
hours (issue #736; ``services/contracts/run_window.py``,
``services/platform/run_window_setting.py``, ``services/loop/run_window_gate.py``).

Every instant comes from a fake clock (``run_window_gate.utc_now``) and every
zone from ``zoneinfo``. With the DeepSeek preset in America/Montevideo a
sprint started on a Tuesday at 22:30 waits until 01:00, one started on a
Saturday at 23:30 starts at once, and a Chinese public holiday from the
shipped calendar is off-peak all day. Each hold and release is a task event;
an urgent sprint starts at once and is audited; only an Owner changes the
window, on an intact audit chain; and a restart mid-wait releases from what
the database holds.
"""

import asyncio
import secrets
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import audit_signing
import pytest
import worker_stages
from audit_chain import verify_chain
from models import AuditLog, BacklogItem, Company, OutboxEvent, Sprint, TaskEvent
from services.contracts import run_window as rw
from services.loop import run_window_gate as gate
from services.loop.worker import intake, task_run
from services.platform import run_window_setting as setting
from sqlmodel import select
from test_git_workflow import _member, enforcement  # noqa: F401

MVD = ZoneInfo("America/Montevideo")
DEEPSEEK = {"timezone": "America/Montevideo", "preset": rw.DEEPSEEK}
# 2026-11-24 is a Tuesday, 2026-11-28 a Saturday.
TUESDAY_2230 = datetime(2026, 11, 24, 22, 30, tzinfo=MVD)
SATURDAY_2330 = datetime(2026, 11, 28, 23, 30, tzinfo=MVD)


@pytest.fixture(autouse=True)
def _audit(monkeypatch, tmp_path):
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "audit"))
    key = secrets.token_hex(16)
    monkeypatch.setattr(audit_signing, "_configured_key", lambda: key)


@pytest.fixture
def clock(monkeypatch):
    """The backend's clock, set by the test."""
    now = {"at": TUESDAY_2230}
    monkeypatch.setattr(gate, "utc_now", lambda: now["at"].astimezone(UTC))
    return now


def _company(session, window=None):
    company = Company(company_name=f"Window {secrets.token_hex(3)}", run_window=window)
    session.add(company)
    session.commit()
    session.refresh(company)
    return company


def _payload(company_id, **extra):
    return {
        "company_id": company_id,
        "sprint_name": "Night sprint",
        "start_date": "2026-11-24",
        "scope_size": "S",
        "backlog_items": [
            {"title": "Task 1", "description": "One"},
            {"title": "Task 2", "description": "Two"},
        ],
        **extra,
    }


def _events(session, sprint_id, kind):
    return session.exec(
        select(TaskEvent)
        .where(TaskEvent.sprint_id == sprint_id, TaskEvent.event_type == kind)
        .order_by(TaskEvent.id)
    ).all()


def _assignments(session, sprint_id):
    rows = session.exec(
        select(OutboxEvent).where(OutboxEvent.aggregate_type == "backlog_item")
    ).all()
    ids = {
        i.id
        for i in session.exec(
            select(BacklogItem).where(BacklogItem.sprint_id == sprint_id)
        ).all()
    }
    return [r for r in rows if int(r.aggregate_id) in ids]


def _kickoff(session, sprint_id):
    return session.exec(
        select(OutboxEvent).where(
            OutboxEvent.aggregate_type == "sprint",
            OutboxEvent.aggregate_id == str(sprint_id),
        )
    ).one()


# --- the window ----------------------------------------------------------------


def test_the_deepseek_preset_in_montevideo_on_a_weekday_and_a_weekend():
    window = rw.validate(DEEPSEEK)
    holidays = setting.holidays_for(window)

    # Tuesday 22:30 local is Wednesday 01:30 UTC: peak until 04:00 UTC.
    assert not rw.is_open(window, TUESDAY_2230, holidays)
    opens = rw.next_open(window, TUESDAY_2230, holidays)
    assert opens.astimezone(MVD) == datetime(2026, 11, 25, 1, 0, tzinfo=MVD)
    # Between the two peaks, then the second one (06:00–10:00 UTC).
    assert rw.is_open(window, datetime(2026, 11, 25, 2, 30, tzinfo=MVD), holidays)
    later = datetime(2026, 11, 25, 3, 30, tzinfo=MVD)
    assert not rw.is_open(window, later, holidays)
    assert rw.next_open(window, later, holidays).astimezone(MVD).hour == 7
    # Saturday 23:30 local is Sunday 02:30 UTC: off-peak all day.
    assert rw.is_open(window, SATURDAY_2330, holidays)
    # The next change from a closed instant is its opening.
    assert rw.next_change(window, TUESDAY_2230, holidays) == opens


def test_a_chinese_public_holiday_from_the_calendar_is_off_peak_all_day():
    window = rw.validate(DEEPSEEK)
    holidays = setting.holidays_for(window)
    # 2026-10-07 (National Day) is a Wednesday: 01:30 UTC that day is open...
    assert rw.is_open(window, datetime(2026, 10, 6, 22, 30, tzinfo=MVD), holidays)
    # ...and 2026-10-08 is an ordinary Thursday again.
    assert not rw.is_open(window, datetime(2026, 10, 7, 22, 30, tzinfo=MVD), holidays)
    # Without the calendar the holiday would be a peak hour.
    assert not rw.is_open(
        window, datetime(2026, 10, 6, 22, 30, tzinfo=MVD), frozenset()
    )
    # The shipped calendars cite their source, per year.
    years = setting.calendars()["cn"]["years"]
    assert [y["year"] for y in years] == [2026, 2027]
    assert all(y["source"]["url"].startswith("https://") for y in years)
    assert datetime(2027, 2, 5).date() in setting.calendar_dates("cn")


def test_an_owner_date_opens_the_window_all_day():
    window = rw.validate({**DEEPSEEK, "holidays": {"dates": ["2026-11-25"]}})
    assert rw.is_open(window, TUESDAY_2230, setting.holidays_for(window))


def test_custom_intervals_run_over_midnight_and_follow_daylight_saving():
    window = rw.validate(
        {
            "timezone": "Europe/Madrid",
            "intervals": [{"days": ["sat"], "start": "22:00", "end": "06:00"}],
        }
    )
    madrid = ZoneInfo("Europe/Madrid")
    none = frozenset()
    # 2026-10-25 is the Sunday the clocks go back: 05:30 is still in Saturday's interval.
    assert rw.is_open(window, datetime(2026, 10, 25, 5, 30, tzinfo=madrid), none)
    assert not rw.is_open(window, datetime(2026, 10, 25, 6, 30, tzinfo=madrid), none)
    # Friday night is not Saturday's.
    assert not rw.is_open(window, datetime(2026, 10, 23, 23, 0, tzinfo=madrid), none)
    opens = rw.next_open(window, datetime(2026, 10, 23, 23, 0, tzinfo=madrid), none)
    assert opens.astimezone(madrid) == datetime(2026, 10, 24, 22, 0, tzinfo=madrid)
    # The span is 9 hours of wall clock but 9 hours of local time over the change:
    # 22:00 CEST to 06:00 CET is 9 real hours.
    [(start, end)] = rw.spans(window, opens, none, days=1)
    assert end - start == timedelta(hours=9)


def test_a_bad_window_is_refused_with_the_reason():
    for raw, words in [
        ({"timezone": "UTC-3", "preset": rw.DEEPSEEK}, "time zone"),
        ({"timezone": "UTC"}, "preset or intervals"),
        ({"timezone": "UTC", "preset": "cheap"}, "preset is one of"),
        (
            {
                "timezone": "UTC",
                "intervals": [{"days": ["mon"], "start": "25:00", "end": "26:00"}],
            },
            "HH:MM",
        ),
        (
            {
                "timezone": "UTC",
                "intervals": [{"days": ["xyz"], "start": "01:00", "end": "02:00"}],
            },
            "not a day",
        ),
        ({**DEEPSEEK, "holidays": {"dates": ["24/11/2026"]}}, "ISO 8601"),
    ]:
        with pytest.raises(rw.InvalidWindow, match=words):
            rw.validate(raw)
    assert rw.validate(None) is None
    assert rw.parse({"timezone": "nowhere"}) is None


# --- who sets it ---------------------------------------------------------------


def test_only_an_owner_changes_the_window_audited_on_an_intact_chain(
    sqlite_session,
    test_client,
    enforcement,  # noqa: F811
    clock,
):
    company = _company(sqlite_session)
    viewer = _member(sqlite_session, company.id, "Viewer")
    manager = _member(sqlite_session, company.id, "Manager")
    owner = _member(sqlite_session, company.id, "Owner")
    url = f"/v1/companies/{company.id}/run_window"

    read = test_client.get(url, headers=viewer)
    assert read.status_code == 200, read.text
    assert read.json()["run_window"] is None and read.json()["open"] is True
    assert read.json()["presets"][rw.DEEPSEEK]["source"].startswith(
        "https://api-docs.deepseek.com"
    )
    for who in (viewer, manager):
        assert (
            test_client.put(url, json={"run_window": DEEPSEEK}, headers=who).status_code
            == 403
        )
    bad = test_client.put(
        url, json={"run_window": {"timezone": "Mars/Base"}}, headers=owner
    )
    assert bad.status_code == 422
    unknown = test_client.put(
        url,
        json={"run_window": {**DEEPSEEK, "holidays": {"calendar": "atlantis"}}},
        headers=owner,
    )
    assert unknown.status_code == 422 and "atlantis" in unknown.json()["detail"]

    changed = test_client.put(url, json={"run_window": DEEPSEEK}, headers=owner)
    assert changed.status_code == 200, changed.text
    body = changed.json()
    assert body["open"] is False
    assert body["next_open"] == "2026-11-25T01:00:00-03:00"
    assert len(body["preview"]) >= 7
    assert (
        test_client.put(url, json={"run_window": DEEPSEEK}, headers=owner).status_code
        == 200
    )
    off = test_client.put(url, json={"run_window": None}, headers=owner)
    assert off.json()["run_window"] is None

    rows = sqlite_session.exec(
        select(AuditLog)
        .where(
            AuditLog.company_id == company.id, AuditLog.action == "run_window_changed"
        )
        .order_by(AuditLog.id)
    ).all()
    assert [r.result["to"] is None for r in rows] == [False, True]
    assert rows[0].result["from"] is None
    assert rows[0].result["to"]["preset"] == rw.DEEPSEEK
    assert rows[0].details["user_id"] is not None
    assert verify_chain(sqlite_session, company.id)["status"] == "intact"

    preview = test_client.post(
        f"{url}/preview",
        json={
            "run_window": {
                "timezone": "UTC",
                "intervals": [{"days": ["mon"], "start": "00:00", "end": "24:00"}],
            }
        },
        headers=viewer,
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["preview"][0]["from"].startswith("2026-11-30T00:00:00")


# --- what it holds back ----------------------------------------------------------


def test_a_sprint_started_on_tuesday_at_2230_waits_until_0100_and_is_released(
    sqlite_session, test_client, clock
):
    company = _company(sqlite_session, DEEPSEEK)
    started = test_client.post("/v1/start_sprint", json=_payload(company.id))
    assert started.status_code == 200, started.text
    body = started.json()
    sprint_id = body["sprint_id"]
    assert body["run_window"]["until_local"] == "2026-11-25T01:00:00-03:00"
    assert body["run_window"]["timezone"] == "America/Montevideo"

    # Nothing is queued: no assignment, the kickoff held, one wait per task.
    sqlite_session.expire_all()
    assert _assignments(sqlite_session, sprint_id) == []
    assert _kickoff(sqlite_session, sprint_id).status == gate.HELD_OUTBOX
    waits = _events(sqlite_session, sprint_id, gate.WAIT)
    assert len(waits) == 2
    assert waits[0].actor == "system"
    assert waits[0].payload["until"] == "2026-11-25T04:00:00+00:00"
    assert waits[0].payload["window"]["preset"] == rw.DEEPSEEK
    assert waits[0].payload["observed_at"] == "2026-11-25T01:30:00+00:00"
    board = test_client.get(f"/v1/sprints/{sprint_id}/board").json()
    assert board["sprint"]["run_window"]["until_local"] == "2026-11-25T01:00:00-03:00"
    inbox = test_client.get("/v1/inbox", params={"company_id": company.id}).json()
    assert [s["sprint_id"] for s in inbox["run_window_waiting"]] == [sprint_id]

    # The worker claims nothing meanwhile, and records no second wait.
    item = waits[0].task_id
    assert gate.hold_task(sqlite_session.get_bind(), item) is True
    sqlite_session.expire_all()
    assert len(_events(sqlite_session, sprint_id, gate.WAIT)) == 2

    engine = sqlite_session.get_bind()
    clock["at"] = datetime(2026, 11, 25, 0, 59, tzinfo=MVD)
    assert gate.release_due(engine) == []
    clock["at"] = datetime(2026, 11, 25, 1, 0, tzinfo=MVD)
    assert gate.release_due(engine) == [sprint_id]

    sqlite_session.expire_all()
    releases = _events(sqlite_session, sprint_id, gate.RELEASE)
    assert len(releases) == 2
    assert releases[0].payload["reason"] == "window_open"
    assert releases[0].payload["released_at"] == "2026-11-25T04:00:00+00:00"
    assert len(_assignments(sqlite_session, sprint_id)) == 2
    assert _kickoff(sqlite_session, sprint_id).status == "pending"
    assert sqlite_session.get(Sprint, sprint_id).run_window_wait_until is None
    assert gate.hold_task(engine, item) is False
    # Neither event says anything about the task's own state.
    assert sqlite_session.get(BacklogItem, item).status == "todo"


def test_a_sprint_started_on_saturday_at_2330_starts_at_once(
    sqlite_session, test_client, clock
):
    clock["at"] = SATURDAY_2330
    company = _company(sqlite_session, DEEPSEEK)
    body = test_client.post("/v1/start_sprint", json=_payload(company.id)).json()
    assert body["run_window"] is None
    sqlite_session.expire_all()
    assert len(_assignments(sqlite_session, body["sprint_id"])) == 2
    assert _kickoff(sqlite_session, body["sprint_id"]).status == "pending"
    assert _events(sqlite_session, body["sprint_id"], gate.WAIT) == []


def test_an_urgent_sprint_starts_at_once_and_the_bypass_is_audited(
    sqlite_session,
    test_client,
    enforcement,  # noqa: F811
    clock,
):
    company = _company(sqlite_session, DEEPSEEK)
    manager = _member(sqlite_session, company.id, "Manager")
    viewer = _member(sqlite_session, company.id, "Viewer")
    body = test_client.post(
        "/v1/start_sprint", json=_payload(company.id, urgent=True), headers=manager
    ).json()
    assert body["urgent"] is True and body["run_window"] is None
    sqlite_session.expire_all()
    assert len(_assignments(sqlite_session, body["sprint_id"])) == 2

    # A waiting sprint marked urgent later is released at once.
    waiting = test_client.post(
        "/v1/start_sprint", json=_payload(company.id), headers=manager
    ).json()
    assert waiting["run_window"] is not None
    url = f"/v1/sprints/{waiting['sprint_id']}/urgent"
    assert (
        test_client.put(url, json={"urgent": True}, headers=viewer).status_code == 403
    )
    marked = test_client.put(url, json={"urgent": True}, headers=manager)
    assert marked.status_code == 200, marked.text
    assert marked.json()["released_tasks"] == 2 and marked.json()["run_window"] is None
    sqlite_session.expire_all()
    [release, _] = _events(sqlite_session, waiting["sprint_id"], gate.RELEASE)
    assert release.payload["reason"] == "urgent"

    rows = sqlite_session.exec(
        select(AuditLog).where(
            AuditLog.company_id == company.id,
            AuditLog.action == gate.URGENT_AUDIT_ACTION,
        )
    ).all()
    assert len(rows) == 2
    assert all(r.details["user_id"] is not None for r in rows)
    assert rows[1].details["waiting"] is True
    assert verify_chain(sqlite_session, company.id)["status"] == "intact"


def test_a_planned_sprint_started_and_a_resume_wait_for_the_window_too(
    sqlite_session, test_client, clock
):
    company = _company(sqlite_session, DEEPSEEK)
    sprint = Sprint(company_id=company.id, sprint_name="Later", status="planned")
    sqlite_session.add(sprint)
    sqlite_session.commit()
    sqlite_session.add(BacklogItem(sprint_id=sprint.id, title="One"))
    sqlite_session.commit()
    started = test_client.post(f"/v1/sprints/{sprint.id}/start")
    assert started.status_code == 200, started.text
    assert started.json()["dispatched"] == 0
    assert started.json()["run_window"]["until_local"] == "2026-11-25T01:00:00-03:00"


def test_a_restart_mid_wait_releases_from_the_database(
    sqlite_session, test_client, clock
):
    company = _company(sqlite_session, DEEPSEEK)
    sprint_id = test_client.post("/v1/start_sprint", json=_payload(company.id)).json()[
        "sprint_id"
    ]
    engine = sqlite_session.get_bind()
    # The worker sleeps to the boundary (150 minutes away), at most 300 s.
    assert gate.seconds_until_next_look(engine) == 300.0
    clock["at"] = datetime(2026, 11, 25, 0, 58, 30, tzinfo=MVD)
    assert gate.seconds_until_next_look(engine) == 90.0
    clock["at"] = datetime(2026, 11, 25, 0, 59, 50, tzinfo=MVD)
    assert gate.seconds_until_next_look(engine) == 60.0  # never tighter than a minute
    # A new process holds nothing in memory: the backend was down past 01:00.
    clock["at"] = datetime(2026, 11, 25, 2, 0, tzinfo=MVD)
    assert gate.release_due(engine) == [sprint_id]
    assert gate.release_due(engine) == []


def test_turning_the_window_off_releases_what_it_held(
    sqlite_session, test_client, clock
):
    company = _company(sqlite_session, DEEPSEEK)
    sprint_id = test_client.post("/v1/start_sprint", json=_payload(company.id)).json()[
        "sprint_id"
    ]
    company = sqlite_session.get(Company, company.id)
    company.run_window = None
    sqlite_session.add(company)
    sqlite_session.commit()
    assert gate.release_due(sqlite_session.get_bind()) == [sprint_id]
    sqlite_session.expire_all()
    [release, _] = _events(sqlite_session, sprint_id, gate.RELEASE)
    assert release.payload["reason"] == "window_removed"


def test_qa_review_of_a_delivered_task_waits_for_the_window(sqlite_session, clock):
    company = _company(sqlite_session, DEEPSEEK)
    sprint = Sprint(company_id=company.id, sprint_name="Review")
    sqlite_session.add(sprint)
    sqlite_session.commit()
    item = BacklogItem(sprint_id=sprint.id, title="Delivered", status="in_review")
    sqlite_session.add(item)
    sqlite_session.commit()
    assert gate.gate_task(sqlite_session, item, sprint) is True
    assert gate.gate_task(sqlite_session, item, sprint) is True
    sqlite_session.commit()
    assert len(_events(sqlite_session, sprint.id, gate.WAIT)) == 1
    assert sqlite_session.get(BacklogItem, item.id).status == "in_review"
    clock["at"] = SATURDAY_2330
    assert gate.gate_task(sqlite_session, item, sprint) is False


class _AssignNats:
    def __init__(self):
        self.queued = []

    async def publish(self, subject, payload, msg_id=None):
        if subject == "task.assign":
            self.queued.append(payload["backlog_item_id"])


async def _no_glassroom(*args, **kwargs):
    return None


def test_the_worker_neither_queues_nor_claims_a_task_outside_the_window(
    sqlite_session, monkeypatch, clock
):
    worker_stages.use_engine(monkeypatch, sqlite_session.get_bind())
    monkeypatch.setattr(intake, "publish_glassroom", _no_glassroom)
    ran = []

    async def _run(nats_client, backlog_item_id, ctx):
        ran.append(backlog_item_id)

    monkeypatch.setattr(task_run, "_run_claimed_task", _run)
    company = _company(sqlite_session, DEEPSEEK)
    sprint = Sprint(company_id=company.id, sprint_name="Running")
    sqlite_session.add(sprint)
    sqlite_session.commit()
    item = BacklogItem(sprint_id=sprint.id, title="Next turn", assignee="Developer")
    sqlite_session.add(item)
    sqlite_session.commit()
    assignment = {"backlog_item_id": item.id, "sprint_id": sprint.id}

    nats = _AssignNats()
    asyncio.run(intake._dispatch_todo_items(nats))
    asyncio.run(intake._on_task_assign(nats, None, assignment))
    sqlite_session.expire_all()
    assert nats.queued == [] and ran == []
    assert sqlite_session.get(BacklogItem, item.id).status == "todo"
    assert len(_events(sqlite_session, sprint.id, gate.WAIT)) == 1

    clock["at"] = SATURDAY_2330
    asyncio.run(intake._dispatch_todo_items(nats))
    asyncio.run(intake._on_task_assign(nats, None, assignment))
    assert nats.queued == [item.id] and ran == [item.id]
