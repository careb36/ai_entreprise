"""A run's full log read back and checked against its record (issue #315).

``routers/run_logs.py`` and ``services/workspace/run_log_reader.py``: the
backend hashes the bytes it serves and compares them with the sha256 it
recorded; no hash from the client, the daemon or the store is used.
"""

import hashlib
from types import SimpleNamespace

import pytest
from models.agent_run import Verification
from models.company import Company
from services.workspace import run_log_reader


def _make_company(session, name):
    company = Company(company_name=name)
    session.add(company)
    session.commit()
    session.refresh(company)
    return company.id


def _payload(company_id):
    return {
        "company_id": company_id,
        "external_ref": "github:example/app#1",
        "producer": "copilot",
        "artifact": "diff contents",
        "claims": {"summary": "opened a PR"},
    }


def _ingest(test_client, monkeypatch, tmp_path, payload):
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path))
    return test_client.post("/v1/agent_runs", json=payload)


class ObjectNotFoundError(Exception):
    pass


class _Store:
    def __init__(self, objects):
        self.objects = objects

    async def get_info(self, name):
        if name not in self.objects:
            raise ObjectNotFoundError(name)
        return SimpleNamespace(size=len(self.objects[name]), deleted=False)

    async def get(self, name, writeinto=None):
        writeinto.write(self.objects[name])


def _nats(monkeypatch, client, objects):
    async def object_store(_bucket):
        return _Store(objects)

    js = SimpleNamespace(object_store=object_store) if objects is not None else None
    for key, value in (("run_logs_bucket", "B"), ("js", js)):
        monkeypatch.setattr(client.app.state.nats_client, key, value, raising=False)


NAME = "run-" + "a" * 32 + "/stdout"
GOOD = hashlib.sha256(b"hello").hexdigest()


def _run_with_log(session, client, monkeypatch, tmp_path, name):
    company_id = _make_company(session, name=f"Logs {name}")
    run = _ingest(client, monkeypatch, tmp_path, _payload(company_id)).json()
    obj = {"stream": "stdout", "object": name, "observed": True, "sha256": GOOD}
    full = {"observed": True, "observed_by": "backend", "objects": [obj]}
    lost = {"observed": False, "reason": "full logs are turned off"}
    evidence = {"test_run": {"full_log": full}, "app_run": {"full_log": lost}}
    row = Verification(
        company_id=company_id, agent_run_id=run["id"], verdict="x", evidence=evidence
    )
    session.add(row)
    session.commit()
    return company_id, f"/v1/agent_runs/{run['id']}/logs", row.id


@pytest.mark.parametrize(
    ("objects", "status", "integrity"),
    [
        ({NAME: b"hello"}, 200, "matched"),
        ({NAME: b"hellO"}, 200, "mismatched"),
        ({}, 410, "missing"),
        (None, 503, "unavailable"),
    ],
)
def test_run_log_is_hashed_over_the_bytes_served(
    sqlite_session, test_client, monkeypatch, tmp_path, objects, status, integrity
):
    company, url, vid = _run_with_log(
        sqlite_session, test_client, monkeypatch, tmp_path, NAME
    )
    _nats(monkeypatch, test_client, objects)
    listed = test_client.get(url, params={"company_id": company}).json()["logs"]
    assert [(e["id"], e["source"], e["openable"]) for e in listed] == [
        (f"{vid}-0", "app_run", False),
        (f"{vid}-1", "test_run", True),
    ]
    # A sha256 the client sends is never read: the bytes' own hash decides.
    forged = {"company_id": company, "download": "true", "sha256": GOOD}
    got = test_client.get(f"{url}/{vid}-1", params=forged)
    assert got.status_code == status
    if status == 200:
        assert got.headers["x-log-integrity"] == integrity
        assert got.headers["x-log-sha256-recorded"] == GOOD
        observed = hashlib.sha256(got.content).hexdigest()
        assert got.headers["x-log-sha256-observed"] == observed
        assert got.headers["x-log-observed-by"] == "backend"
        assert "attachment" in got.headers["content-disposition"]
    else:
        assert got.json()["detail"]["integrity"]["status"] == integrity
    not_kept = test_client.get(f"{url}/{vid}-0", params={"company_id": company})
    assert not_kept.status_code == 409


def test_run_log_refuses_other_companies_and_foreign_objects(
    sqlite_session, test_client, monkeypatch, tmp_path
):
    company, url, vid = _run_with_log(
        sqlite_session, test_client, monkeypatch, tmp_path, "o/out"
    )
    outsider = _make_company(sqlite_session, name="Logs outsider")
    _nats(monkeypatch, test_client, {"o/out": b"hello"})
    assert test_client.get(url, params={"company_id": outsider}).status_code == 404
    log = f"{url}/{vid}-1"
    assert test_client.get(log, params={"company_id": outsider}).status_code == 404
    # A name of a shape the backend never asks for is never opened.
    assert test_client.get(log, params={"company_id": company}).status_code == 409
    unknown = test_client.get(f"{url}/x-1", params={"company_id": company})
    assert unknown.status_code == 404


def test_a_log_over_the_cap_is_unavailable_never_partly_hashed():
    sink = run_log_reader._HashingSpool(cap=4)
    sink.write(b"abcd")
    with pytest.raises(run_log_reader.LogUnavailable):
        sink.write(b"e")
    sink.spool.close()
