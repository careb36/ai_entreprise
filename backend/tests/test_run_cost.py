"""Cost per run, task and sprint in tokens and US cents (``services/run_cost.py``).

Only usage the provider reported is priced; an estimate is reported apart and
never priced; a model the catalogue does not price is ``cost_cents: None``
with the reason, never 0; every number is the company's own.
"""

import secrets
from datetime import UTC, datetime, timedelta

import auth_tokens
import pytest
from config import settings
from models import AuditLog, BacklogItem, Company, CompanyMember, Sprint, User
from models.agent_run import TASK_WORKER_PRODUCER, AgentRun
from models.provider import ProviderConnection
from services.models import model_catalog, model_prices, run_cost
from services.platform.orchestration_metrics import record_agent_turn
from services.platform.token_usage import usage_fields

CATALOG = {
    "source": "models.dev",
    "providers": {
        "deepseek": {
            "models": {"coder": {"cost_input": 1.0, "cost_output": 4.0}},
        },
        "ollama-cloud": {
            "models": {
                "coder": {"cost_input": 1.0, "cost_output": 4.0},
                "split": {"cost_input": 2.0, "cost_output": 2.0},
            },
        },
        "google": {
            "models": {
                "flash": {"cost_input": 0.5, "cost_output": 2.0},
                "split": {"cost_input": 3.0, "cost_output": 3.0},
                "free": {"cost_input": 0, "cost_output": 0},
                "noprice": {"context": 1000},
            },
        },
    },
}


@pytest.fixture(autouse=True)
def fixed_catalog(monkeypatch):
    monkeypatch.setattr(model_catalog, "catalog", lambda: CATALOG)


@pytest.fixture
def signing_key(monkeypatch):
    key = secrets.token_urlsafe(48)
    monkeypatch.setattr(auth_tokens, "_configured_key", lambda: key)
    monkeypatch.setattr(settings, "AUTH_REQUIRED", True)
    return key


# --- the price ------------------------------------------------------------


def test_the_proxy_provider_names_the_catalogue_entry():
    price, reason = model_prices.price_for("flash", "gemini")
    assert reason is None
    assert (price["provider"], price["input"], price["output"]) == ("google", 0.5, 2.0)


def test_a_provider_prefix_in_the_model_id_names_the_entry():
    price, _ = model_prices.price_for("google/split")
    assert (price["provider"], price["model"], price["input"]) == (
        "google",
        "split",
        3.0,
    )


def test_a_bare_model_listed_at_one_price_is_priced():
    price, reason = model_prices.price_for("coder")
    assert reason is None and price["input"] == 1.0


def test_one_model_at_two_prices_is_ambiguous():
    assert model_prices.price_for("split") == (None, model_prices.PRICE_AMBIGUOUS)


def test_a_model_without_a_price_is_unknown_never_free():
    assert model_prices.price_for("noprice") == (None, model_prices.PRICE_UNKNOWN)
    assert model_prices.price_for("nowhere") == (None, model_prices.PRICE_UNKNOWN)
    assert model_prices.price_for("") == (None, model_prices.NO_MODEL)
    free, _ = model_prices.price_for("free", "gemini")
    assert free["input"] == 0.0


def test_a_known_provider_that_does_not_price_the_model_is_not_priced_by_another():
    assert model_prices.price_for("coder", "gemini") == (
        None,
        model_prices.PRICE_UNKNOWN,
    )
    # A local server through an openai-compatible connection is never a cloud price.
    assert model_prices.price_for("coder", None, "openai-compatible") == (
        None,
        model_prices.PRICE_UNKNOWN,
    )


def test_the_default_team_is_priced_at_deepseeks_published_peak_price():
    """DeepSeek's pricing page (read 2026-11-19), peak hours, cache miss."""
    flash, reason = model_prices.price_for("deepseek-flash", "deepseek")
    assert reason is None
    assert (flash["input"], flash["output"]) == (0.30, 1.20)
    pro, _ = model_prices.price_for("deepseek-v4-pro", "deepseek")
    assert (pro["input"], pro["output"]) == (1.32, 3.96)
    # The retired Flash name is served, and billed, as deepseek-flash.
    legacy, _ = model_prices.price_for("deepseek-v4-flash", "deepseek")
    assert (legacy["model"], legacy["input"], legacy["output"]) == (
        "deepseek-flash",
        0.30,
        1.20,
    )
    # Another provider's copy of the same name keeps its own price.
    assert model_prices.price_for("deepseek-flash", "gemini") == (
        None,
        model_prices.PRICE_UNKNOWN,
    )


def test_cents_are_dollars_per_million_times_one_hundred():
    price = {"input": 1.0, "output": 4.0}
    assert model_prices.cents(1_000_000, 0, price) == pytest.approx(100.0)
    assert model_prices.cents(1000, 500, price) == pytest.approx(0.3)


# --- what a turn keeps ----------------------------------------------------


def test_usage_fields_keep_the_reported_split_and_the_estimate_apart():
    reported = {
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        "usage_status": "reported",
        "provider": "deepseek",
    }
    assert usage_fields(reported) == {
        "provider": "deepseek",
        "input_tokens": 10,
        "output_tokens": 5,
    }
    estimated = {"usage_status": "estimated", "usage_estimate": 40}
    assert usage_fields(estimated) == {"estimated_tokens": 40}
    assert usage_fields({"usage": {"total_tokens": 15}}) == {}
    assert usage_fields(None) == {}


def test_record_agent_turn_writes_the_split_beside_the_total(sqlite_session):
    company = _company(sqlite_session, "Turn writer")
    row = record_agent_turn(
        company_id=company.id,
        sprint_id=None,
        backlog_item_id=7,
        agent={"role": "Developer", "model": "coder"},
        action="execute_task",
        llm_result={
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "total_tokens": 120,
            },
            "usage_status": "reported",
            "provider": "deepseek",
        },
        session=sqlite_session,
    )
    assert row.cost_tokens == 120
    assert row.details["tokens"] == "reported"
    assert (row.details["input_tokens"], row.details["output_tokens"]) == (100, 20)
    assert row.details["provider"] == "deepseek"


# --- summing turns --------------------------------------------------------


# A Monday in DeepSeek's peak hours, so the full rate whatever day the test runs.
PEAK = datetime(2026, 11, 2, 7, 0)


def _turn(details, tokens=0):
    return AuditLog(
        action="execute_task", details=details, cost_tokens=tokens, created_at=PEAK
    )


def test_reported_turns_are_priced_and_estimates_left_out_of_the_cents():
    turns = [
        run_cost.turn_cost(
            _turn(
                {
                    "tokens": "reported",
                    "model": "coder",
                    "provider": "deepseek",
                    "input_tokens": 1000,
                    "output_tokens": 500,
                },
                1500,
            ),
            {},
        ),
        run_cost.turn_cost(
            _turn({"tokens": "estimated", "model": "coder", "estimated_tokens": 9000}),
            {},
        ),
    ]
    total = run_cost.summarize(turns)
    assert total["cost_cents"] == pytest.approx(0.3)
    assert (total["input_tokens"], total["output_tokens"]) == (1000, 500)
    assert total["reported_tokens"] == 1500
    assert (total["estimated_tokens"], total["estimated_turns"]) == (9000, 1)
    assert total["models"] == ["coder"] and total["reason"] is None


def test_an_unpriced_model_makes_the_cost_null_with_the_reason():
    turns = [
        run_cost.turn_cost(
            _turn(
                {
                    "tokens": "reported",
                    "model": "noprice",
                    "provider": "gemini",
                    "input_tokens": 10,
                    "output_tokens": 10,
                },
                20,
            ),
            {},
        )
    ]
    total = run_cost.summarize(turns)
    assert total["cost_cents"] is None
    assert total["reason"] == model_prices.PRICE_UNKNOWN
    assert total["unpriced_models"] == ["noprice"]
    assert total["reported_tokens"] == 20


def test_a_turn_without_its_split_or_without_reported_usage_is_not_priced():
    old = run_cost.turn_cost(_turn({"model": "coder"}, 50), {})
    assert run_cost.summarize([old])["reason"] == run_cost.SPLIT_UNKNOWN
    estimated_only = run_cost.turn_cost(
        _turn({"tokens": "estimated", "model": "coder", "estimated_tokens": 3}), {}
    )
    total = run_cost.summarize([estimated_only])
    assert (total["cost_cents"], total["reason"]) == (None, run_cost.NO_REPORTED_USAGE)
    assert run_cost.summarize([])["reason"] == run_cost.NO_TURNS


def test_a_company_connection_prices_by_its_provider():
    details = {
        "tokens": "reported",
        "model": "flash",
        "provider": "connection:mine",
        "input_tokens": 2000,
        "output_tokens": 0,
    }
    priced = run_cost.turn_cost(_turn(details, 2000), {"mine": "google"})
    assert priced["cents"] == pytest.approx(0.1)
    gone = run_cost.turn_cost(_turn(details, 2000), {})
    assert gone["reason"] == model_prices.PRICE_UNKNOWN


# --- per run, task and sprint through the API ----------------------------


def _company(session, name):
    company = Company(company_name=name)
    session.add(company)
    session.commit()
    return company


def _headers(session, company_id, role="Viewer"):
    user = User(
        email=f"cost-{secrets.token_hex(8)}@example.com",
        full_name="Cost reader",
        password_hash="unused",
    )
    session.add(user)
    session.commit()
    if company_id is not None:
        session.add(
            CompanyMember(company_id=company_id, user_id=user.id, human_role=role)
        )
        session.commit()
    token = auth_tokens.create_access_token(user_id=user.id, email=user.email)
    return {"Authorization": f"Bearer {token}"}


def _audit(session, company_id, task_id, at, tokens_in, tokens_out, **extra):
    details = {
        "tokens": "reported",
        "model": "coder",
        "provider": "deepseek",
        "input_tokens": tokens_in,
        "output_tokens": tokens_out,
        **extra,
    }
    session.add(
        AuditLog(
            company_id=company_id,
            interaction_id=f"task-{task_id}",
            action="execute_task",
            details=details,
            cost_tokens=tokens_in + tokens_out,
            created_at=at,
        )
    )


def _run(session, company_id, task_id, version, at):
    run = AgentRun(
        company_id=company_id,
        external_ref=f"task-{task_id}",
        version=version,
        producer=TASK_WORKER_PRODUCER,
        artifact_digest="0" * 64,
        artifact_size=1,
        ingested_at=at,
    )
    session.add(run)
    session.commit()
    return run


@pytest.fixture
def costed(sqlite_session):
    """Two tasks in one sprint; task one has two run versions and a QA review."""
    s = sqlite_session
    company = _company(s, "Cost company")
    other = _company(s, "Cost other tenant")
    sprint = Sprint(company_id=company.id, sprint_name="Costly")
    s.add(sprint)
    s.commit()
    one = BacklogItem(sprint_id=sprint.id, title="One")
    two = BacklogItem(sprint_id=sprint.id, title="Two")
    s.add_all([one, two])
    s.commit()
    # A Monday in DeepSeek's peak hours (06:00-10:00 UTC): the full rate.
    t0 = datetime(2026, 11, 2, 7, 0, tzinfo=UTC)
    _audit(s, company.id, one.id, t0, 1000, 500)  # v1's delivery: 0.3 cents
    first = _run(
        s, company.id, one.id, 1, (t0 + timedelta(minutes=1)).replace(tzinfo=None)
    )
    _audit(s, company.id, one.id, t0 + timedelta(minutes=2), 2000, 0)  # v2: 0.2
    second = _run(
        s, company.id, one.id, 2, (t0 + timedelta(minutes=3)).replace(tzinfo=None)
    )
    _audit(s, company.id, one.id, t0 + timedelta(minutes=4), 0, 250)  # QA of v2: 0.1
    _audit(s, company.id, two.id, t0, 500, 0)  # 0.05
    s.add(
        AuditLog(
            company_id=company.id,
            interaction_id=f"task-{two.id}",
            action="qa_review",
            details={"tokens": "estimated", "model": "coder", "estimated_tokens": 777},
            cost_tokens=0,
            created_at=t0,
        )
    )
    # Another company's rows naming the same task are never counted.
    _audit(s, other.id, one.id, t0, 10**6, 10**6)
    s.commit()
    return company, other, sprint, one, two, first, second


def test_each_run_version_carries_the_turns_that_led_to_it(
    test_client, sqlite_session, signing_key, costed
):
    company, _, _, _, _, first, second = costed
    headers = _headers(sqlite_session, company.id)
    v1 = test_client.get(
        f"/v1/agent_runs/{first.id}/cost",
        params={"company_id": company.id},
        headers=headers,
    )
    v2 = test_client.get(
        f"/v1/agent_runs/{second.id}/cost",
        params={"company_id": company.id},
        headers=headers,
    )
    assert v1.status_code == 200, v1.text
    assert v1.json()["cost_cents"] == pytest.approx(0.3)
    assert (v1.json()["input_tokens"], v1.json()["output_tokens"]) == (1000, 500)
    assert v2.json()["cost_cents"] == pytest.approx(0.3)  # its delivery and its review
    assert v2.json()["turns"] == 2


def test_a_task_sums_its_runs_and_a_sprint_its_tasks(
    test_client, sqlite_session, signing_key, costed
):
    company, _, sprint, one, two, _, _ = costed
    headers = _headers(sqlite_session, company.id)
    task = test_client.get(f"/v1/tasks/{one.id}/cost", headers=headers).json()
    assert task["cost_cents"] == pytest.approx(0.6)
    assert sum(r["cost_cents"] for r in task["runs"]) == pytest.approx(
        task["cost_cents"]
    )
    assert task["reported_tokens"] == 3750
    detail = test_client.get(f"/v1/sprints/{sprint.id}/cost", headers=headers).json()
    assert detail["cost_cents"] == pytest.approx(0.65)
    assert detail["estimated_tokens"] == 777
    by_task = {t["task_id"]: t for t in detail["tasks"]}
    assert by_task[two.id]["cost_cents"] == pytest.approx(0.05)
    assert by_task[two.id]["estimated_turns"] == 1
    listed = test_client.get(
        "/v1/sprints", params={"company_id": company.id}, headers=headers
    ).json()
    assert listed[0]["cost"]["cost_cents"] == pytest.approx(0.65)


def test_costs_are_scoped_to_the_company(
    test_client, sqlite_session, signing_key, costed
):
    company, other, sprint, one, _, first, _ = costed
    outsider = _headers(sqlite_session, other.id)
    assert (
        test_client.get(f"/v1/tasks/{one.id}/cost", headers=outsider).status_code == 403
    )
    assert (
        test_client.get(f"/v1/sprints/{sprint.id}/cost", headers=outsider).status_code
        == 403
    )
    # A run asked for under another company is not found there.
    run = test_client.get(
        f"/v1/agent_runs/{first.id}/cost",
        params={"company_id": other.id},
        headers=outsider,
    )
    assert run.status_code == 404
    assert test_client.get(f"/v1/tasks/{one.id}/cost").status_code == 401
    # The other tenant's own list counts nothing of ours.
    mine = _headers(sqlite_session, company.id)
    assert (
        test_client.get(f"/v1/tasks/{one.id}/cost", headers=mine).json()[
            "reported_tokens"
        ]
        == 3750
    )


def test_a_run_from_another_producer_has_no_turns_here(
    test_client, sqlite_session, signing_key
):
    company = _company(sqlite_session, "Outside producer")
    run = AgentRun(
        company_id=company.id,
        external_ref="pr-1",
        producer="someone-else",
        artifact_digest="0" * 64,
        artifact_size=1,
    )
    sqlite_session.add(run)
    sqlite_session.commit()
    body = test_client.get(
        f"/v1/agent_runs/{run.id}/cost",
        params={"company_id": company.id},
        headers=_headers(sqlite_session, company.id),
    ).json()
    assert (body["cost_cents"], body["reason"], body["turns"]) == (
        None,
        run_cost.NO_TURNS,
        0,
    )


def test_a_company_connection_row_prices_the_sprint(sqlite_session):
    company = _company(sqlite_session, "Connected")
    sqlite_session.add(
        ProviderConnection(
            company_id=company.id,
            provider="google",
            name="mine",
            base_url="https://x.invalid",
        )
    )
    sprint = Sprint(company_id=company.id, sprint_name="Connected sprint")
    sqlite_session.add(sprint)
    sqlite_session.commit()
    item = BacklogItem(sprint_id=sprint.id, title="t")
    sqlite_session.add(item)
    sqlite_session.commit()
    _audit(
        sqlite_session,
        company.id,
        item.id,
        datetime.now(UTC),
        2000,
        0,
        model="flash",
        provider="connection:mine",
    )
    sqlite_session.commit()
    totals = run_cost.sprint_costs(sqlite_session, company.id, [sprint.id])
    assert totals[sprint.id]["cost_cents"] == pytest.approx(0.1)


# --- the rate in force when the call was made ---------------------------------


def _deepseek_turn(at):
    return AuditLog(
        action="execute_task",
        details={
            "tokens": "reported",
            "model": "deepseek-flash",
            "provider": "deepseek",
            "input_tokens": 1_000_000,
            "output_tokens": 1_000_000,
        },
        cost_tokens=2_000_000,
        created_at=at,
    )


def test_a_deepseek_call_off_peak_costs_half():
    # Published: 0.30 in + 1.20 out per million at peak, half off-peak.
    peak = run_cost.turn_cost(_deepseek_turn(datetime(2026, 11, 2, 7, 0)), {}, {})
    assert peak["cents"] == pytest.approx(150.0) and "off_peak" not in peak
    # 15:00 UTC on a weekday, a Saturday, and a Chinese holiday at peak time.
    for at in (
        datetime(2026, 11, 2, 15, 0),
        datetime(2026, 11, 7, 7, 0),
        datetime(2026, 10, 1, 7, 0, tzinfo=UTC),
    ):
        turn = run_cost.turn_cost(_deepseek_turn(at), {}, {})
        assert turn["cents"] == pytest.approx(75.0) and turn["off_peak"], at
    assert run_cost.summarize([peak, turn])["off_peak_turns"] == 1


def test_a_call_without_a_time_or_another_provider_is_charged_in_full():
    no_time = run_cost.turn_cost(_deepseek_turn(None), {}, {})
    assert no_time["cents"] == pytest.approx(150.0) and "off_peak" not in no_time
    price = {"input": 1.0, "output": 1.0, "provider": "alibaba", "model": "qwen"}
    charged, off_peak = model_prices.at_time(price, datetime(2026, 11, 7, 7, 0), {})
    assert charged == price and off_peak is False
