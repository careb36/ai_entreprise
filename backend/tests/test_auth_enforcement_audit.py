"""The company an audit, lineage or memory request acts for comes from the
authorized context, never the payload: the lineage query by the company
column, another company's interaction refused, the chain's verification
hiding a non-member's rows, the audit row's company set by the server, the
memory search and store scoped by membership, and the audit chain read by a
member of the company only. The shared fixtures are in
``auth_enforcement_helpers``.
"""

import secrets
from unittest.mock import MagicMock

import pytest
from config import settings
from models import (
    AuditLog,
    Company,
)
from sqlmodel import select

import auth_enforcement_helpers
from auth_enforcement_helpers import _member, _run

# The shared fixtures.
enforcement = auth_enforcement_helpers.enforcement
signing_key = auth_enforcement_helpers.signing_key
identity = auth_enforcement_helpers.identity


@pytest.mark.parametrize("enabled", [True, False])
def test_lineage_query_uses_company_column(
    test_client, sqlite_session, identity, monkeypatch, enabled
):
    monkeypatch.setattr(settings, "AUTH_REQUIRED", enabled)
    _member(sqlite_session, identity, "Viewer")
    driver = MagicMock()
    neo_session = driver.session.return_value.__enter__.return_value
    neo_session.run.return_value = [{"engram_id": "owned-engram"}]
    monkeypatch.setattr(settings, "NEO4J_ENABLED", True)
    monkeypatch.setattr(
        "services.platform.memory_graph.get_neo4j_driver", lambda: driver
    )

    response = test_client.get(
        "/v1/enterprise/lineage",
        params={"file_path": "src/main.py", "company_id": identity[1].id},
        headers=identity[2] if enabled else {},
    )

    assert response.status_code == 200
    assert response.json()["lineage"] == [{"engram_id": "owned-engram"}]
    query = neo_session.run.call_args.args[0]
    params = neo_session.run.call_args.kwargs
    assert params["file_path"] == "src/main.py"
    assert params["company_id"] == identity[1].id
    assert "e.company_id = $company_id" in query
    assert "apoc.convert.fromJsonMap" not in query
    assert "e.company_id IS NULL AS legacy" in query


@pytest.mark.parametrize("existing_audit", [False, True])
def test_audit_rejects_another_company_interaction(
    test_client, sqlite_session, identity, existing_audit
):
    _member(sqlite_session, identity, "Manager")
    other = Company(company_name="Other audit owner")
    sqlite_session.add(other)
    sqlite_session.commit()
    run = _run(sqlite_session, other.id)
    interaction_id = f"agent-run:{run.external_ref}"
    if existing_audit:
        sqlite_session.add(
            AuditLog(
                company_id=other.id, interaction_id=interaction_id, action="original"
            )
        )
        sqlite_session.commit()
    response = test_client.post(
        "/v1/audit",
        params={"company_id": identity[1].id},
        json={
            "interaction_id": interaction_id,
            "action": "forged",
            "details": {"company_id": identity[1].id},
        },
        headers=identity[2],
    )
    assert response.status_code == 403
    assert not sqlite_session.exec(
        select(AuditLog).where(
            AuditLog.interaction_id == interaction_id, AuditLog.action == "forged"
        )
    ).all()


def test_audit_verify_hides_nonmember_rows_and_flags_legacy(
    test_client, sqlite_session, identity
):
    _member(sqlite_session, identity, "Viewer")
    other = Company(company_name="Private audit owner")
    sqlite_session.add(other)
    sqlite_session.commit()
    interaction_id = secrets.token_hex(16)
    sqlite_session.add(
        AuditLog(company_id=other.id, interaction_id=interaction_id, action="private")
    )
    sqlite_session.commit()
    params = {"interaction_id": interaction_id}
    assert (
        test_client.get(
            "/v1/audit/verify", params=params, headers=identity[2]
        ).status_code
        == 404
    )
    legacy = AuditLog(interaction_id=interaction_id, action="legacy")
    sqlite_session.add(legacy)
    sqlite_session.commit()
    response = test_client.get("/v1/audit/verify", params=params, headers=identity[2])
    assert response.status_code == 200
    assert response.json()["rows"] == [
        {
            "id": legacy.id,
            "action": "legacy",
            "status": "legacy",
            "mode": "legacy",
            "company_id": None,
            "legacy": True,
        }
    ]
    assert (
        test_client.get(
            "/v1/audit/verify",
            params={**params, "company_id": identity[1].id},
            headers=identity[2],
        ).status_code
        == 404
    )


def test_audit_write_sets_authorized_company_column(
    test_client, sqlite_session, identity
):
    _member(sqlite_session, identity, "Manager")
    interaction_id = secrets.token_hex(16)
    response = test_client.post(
        "/v1/audit",
        params={"company_id": identity[1].id},
        json={"interaction_id": interaction_id, "action": "owned"},
        headers=identity[2],
    )
    assert response.status_code == 200
    row = sqlite_session.exec(
        select(AuditLog).where(AuditLog.interaction_id == interaction_id)
    ).one()
    assert row.company_id == identity[1].id
    response = test_client.get(
        "/v1/audit/verify",
        params={"interaction_id": interaction_id},
        headers=identity[2],
    )
    assert response.json()["rows"][0]["legacy"] is False


@pytest.mark.parametrize("operation", ["audit", "memory/store?content=test"])
def test_payload_company_cannot_override_authorized_context(
    test_client, sqlite_session, identity, operation
):
    _member(sqlite_session, identity, "Manager")
    payload = {"company_id": identity[1].id + 100000}
    if operation == "audit":
        payload = {"action": "forged", "details": payload}
    sep = "&" if "?" in operation else "?"
    response = test_client.post(
        f"/v1/{operation}{sep}company_id={identity[1].id}",
        json=payload,
        headers=identity[2],
    )
    assert response.status_code == 403


@pytest.mark.parametrize("company_filter", [False, True])
def test_memory_search_passes_membership_scope(
    test_client, sqlite_session, identity, company_filter
):
    _member(sqlite_session, identity, "Viewer")
    memory = MagicMock()
    memory.search_similar.return_value = []
    test_client.app.state.memory_client = memory
    params = {"query": "architecture"}
    if company_filter:
        params["company_id"] = identity[1].id
    response = test_client.get("/v1/memory/search", params=params, headers=identity[2])
    assert response.status_code == 200
    memory.search_similar.assert_called_once_with(
        "architecture",
        10,
        company_ids=[identity[1].id],
        include_legacy=not company_filter,
    )


def test_memory_store_passes_server_company(test_client, sqlite_session, identity):
    _member(sqlite_session, identity, "Manager")
    memory = MagicMock()
    test_client.app.state.memory_client = memory
    response = test_client.post(
        "/v1/memory/store",
        params={"content": "decision", "company_id": identity[1].id},
        json={"source": "test"},
        headers=identity[2],
    )
    assert response.status_code == 200
    memory.store.assert_called_once_with(
        "decision", {"source": "test"}, company_id=identity[1].id
    )


def test_memory_store_rejects_foreign_interaction(
    test_client, sqlite_session, identity
):
    _member(sqlite_session, identity, "Manager")
    other = Company(company_name="Private memory owner")
    sqlite_session.add(other)
    sqlite_session.commit()
    run = _run(sqlite_session, other.id)
    response = test_client.post(
        "/v1/memory/store",
        params={"content": "forged", "company_id": identity[1].id},
        json={"interaction_id": f"agent-run:{run.external_ref}"},
        headers=identity[2],
    )
    assert response.status_code == 403


def test_audit_chain_is_read_by_a_member_of_the_company_only(
    test_client, sqlite_session, identity
):
    params = {"company_id": identity[1].id}
    response = test_client.get("/v1/audit/chain", params=params, headers=identity[2])
    assert response.status_code == 403
    _member(sqlite_session, identity, "Viewer")
    response = test_client.get("/v1/audit/chain", params=params, headers=identity[2])
    assert response.status_code == 200
    assert response.json()["company_id"] == identity[1].id
