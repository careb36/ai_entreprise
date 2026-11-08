"""Run secrets (``services/run_secrets.py``, ``/v1/run-secrets``; issue #357):
a secret belongs to one task of one company, is stored only encrypted, names
an environment variable, is listed by the task's company only, and is handed
to the run of that task alone."""

import secrets

import crypto
import pytest
from cryptography.fernet import Fernet
from models import BacklogItem, Company, RunSecret, Sprint
from services import run_secrets
from test_git_workflow import _member, enforcement  # noqa: F401

KEY = Fernet.generate_key().decode()


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setattr(crypto, "_configured_key", lambda: KEY)


def _task(session):
    company = Company(company_name=f"C {secrets.token_hex(3)}", autonomy_level=2)
    session.add(company)
    session.commit()
    sprint = Sprint(company_id=company.id, sprint_name="S", status="active")
    session.add(sprint)
    session.commit()
    item = BacklogItem(sprint_id=sprint.id, title="T")
    session.add(item)
    session.commit()
    return company, item


def test_create_encrypts_and_the_run_gets_the_value(sqlite_session):
    company, item = _task(sqlite_session)
    secret = run_secrets.create(
        sqlite_session, company.id, item.id, "TOKEN", "plain secret!"
    )
    stored = sqlite_session.get(RunSecret, secret.id).encrypted_value
    assert stored.startswith(crypto.FERNET_PREFIX) and "plain secret!" not in stored
    assert run_secrets.env_for_task(sqlite_session, item.id) == {
        "TOKEN": "plain secret!"
    }


def test_nothing_is_stored_without_a_key(sqlite_session, monkeypatch):
    company, item = _task(sqlite_session)
    monkeypatch.setattr(crypto, "_configured_key", lambda: None)
    with pytest.raises(run_secrets.RunSecretError, match="FERNET_SECRET_KEY"):
        run_secrets.create(sqlite_session, company.id, item.id, "TOKEN", "v")
    assert run_secrets.env_for_task(sqlite_session, item.id) == {}


def test_only_a_task_of_the_company_and_only_its_secrets(sqlite_session):
    company, item = _task(sqlite_session)
    other, _ = _task(sqlite_session)
    # A row another company left on the task (from before the check) is not handed over.
    sqlite_session.add(
        RunSecret(
            company_id=other.id,
            task_id=item.id,
            name="LEAK",
            encrypted_value=crypto.encrypt_value("x"),
        )
    )
    sqlite_session.commit()
    run_secrets.create(sqlite_session, company.id, item.id, "TOKEN", "v")
    assert run_secrets.env_for_task(sqlite_session, item.id) == {"TOKEN": "v"}


def test_an_undecryptable_secret_is_left_out_and_logged(sqlite_session, monkeypatch):
    logged = []
    monkeypatch.setattr(
        run_secrets._logger, "warning", lambda msg, *args: logged.append(msg % args)
    )
    company, item = _task(sqlite_session)
    run_secrets.create(sqlite_session, company.id, item.id, "GOOD", "v")
    sqlite_session.add(
        RunSecret(
            company_id=company.id,
            task_id=item.id,
            name="BAD",
            encrypted_value="fernet:not-a-token",
        )
    )
    sqlite_session.commit()
    assert run_secrets.env_for_task(sqlite_session, item.id) == {"GOOD": "v"}
    assert (
        len(logged) == 1
        and "(BAD)" in logged[0]
        and "could not be decrypted" in logged[0]
    )


def test_delete(sqlite_session):
    company, item = _task(sqlite_session)
    other, _ = _task(sqlite_session)
    secret = run_secrets.create(sqlite_session, company.id, item.id, "TOKEN", "v")
    assert not run_secrets.delete(sqlite_session, secret.id, other.id)
    assert run_secrets.delete(sqlite_session, secret.id, company.id)
    assert run_secrets.env_for_task(sqlite_session, item.id) == {}


@pytest.mark.usefixtures("enforcement")
def test_the_api_binds_each_secret_to_the_tasks_company(sqlite_session, test_client):
    company, item = _task(sqlite_session)
    other, theirs = _task(sqlite_session)
    manager = _member(sqlite_session, company.id, "Manager")
    viewer = _member(sqlite_session, company.id, "Viewer")
    outsider = _member(sqlite_session, other.id, "Viewer")
    body = {"company_id": company.id, "task_id": item.id, "name": "TOKEN"}
    # A Manager of the company cannot attach a secret to another company's task.
    cross = test_client.post(
        "/v1/run-secrets",
        json={**body, "task_id": theirs.id, "value": "v"},
        headers=manager,
    )
    assert cross.status_code == 404
    assert (
        test_client.post(
            "/v1/run-secrets", json={**body, "value": "v"}, headers=viewer
        ).status_code
        == 403
    )
    created = test_client.post(
        "/v1/run-secrets", json={**body, "value": "top-secret"}, headers=manager
    )
    assert created.status_code == 200 and "top-secret" not in created.text
    bad = test_client.post(
        "/v1/run-secrets", json={**body, "name": "A B", "value": "v"}, headers=manager
    )
    assert bad.status_code == 422
    listed = test_client.get(f"/v1/run-secrets?task_id={item.id}", headers=viewer)
    assert listed.status_code == 200
    assert [s["name"] for s in listed.json()["secrets"]] == ["TOKEN"]
    assert "top-secret" not in listed.text
    # Another company's member is refused even when the task has no secrets,
    # and an unknown task is not a quiet empty list.
    assert (
        test_client.get(
            f"/v1/run-secrets?task_id={item.id}", headers=outsider
        ).status_code
        == 403
    )
    empty = _task(sqlite_session)[1]
    assert (
        test_client.get(
            f"/v1/run-secrets?task_id={empty.id}", headers=outsider
        ).status_code
        == 403
    )
    assert (
        test_client.get("/v1/run-secrets?task_id=999999", headers=viewer).status_code
        == 404
    )
