"""API tokens for external callers (services.platform.api_tokens)."""

import secrets

import auth_tokens
import pytest
from auth_tokens import create_access_token
from config import settings
from models import AuditLog, Company, CompanyMember, Team, TeamMember, User
from services.platform import api_tokens
from sqlmodel import select


def _company(session):
    company = Company(company_name=f"C {secrets.token_hex(3)}", autonomy_level=2)
    session.add(company)
    session.commit()
    return company


def test_create_and_verify_token(sqlite_session):
    company = _company(sqlite_session)
    token, raw = api_tokens.create_token(sqlite_session, company.id, "ci")
    assert raw.startswith("fab_")
    found = api_tokens.verify(sqlite_session, raw)
    assert found is not None and found.id == token.id


def test_revoke_token(sqlite_session):
    company = _company(sqlite_session)
    token, raw = api_tokens.create_token(sqlite_session, company.id, "ci")
    assert api_tokens.revoke(sqlite_session, token.id, company.id)
    assert api_tokens.verify(sqlite_session, raw) is None


@pytest.fixture()
def signing_key(monkeypatch):
    key = secrets.token_urlsafe(48)
    monkeypatch.setattr(auth_tokens, "_configured_key", lambda: key)
    return key


def _headers(session, company_id, role="Manager"):
    user = User(
        email=f"t-{secrets.token_hex(4)}@x.com", full_name="u", password_hash="x"
    )
    session.add(user)
    session.commit()
    session.add(CompanyMember(company_id=company_id, user_id=user.id, human_role=role))
    session.commit()
    return {
        "Authorization": f"Bearer {create_access_token(user_id=user.id, email=user.email)}"
    }


def test_api_token_http_create_and_list(
    test_client, sqlite_session, signing_key, monkeypatch
):
    monkeypatch.setattr(settings, "AUTH_REQUIRED", True)
    monkeypatch.setattr(auth_tokens, "_configured_key", lambda: signing_key)
    company = _company(sqlite_session)
    headers = _headers(sqlite_session, company.id, "Manager")
    r = test_client.post(
        "/v1/auth/tokens",
        json={"company_id": company.id, "name": "ci"},
        headers=headers,
    )
    assert r.status_code == 200
    assert "token" in r.json()
    r2 = test_client.get(f"/v1/auth/tokens?company_id={company.id}", headers=headers)
    assert r2.status_code == 200
    assert len(r2.json()["tokens"]) == 1


def _mint(test_client, headers, company_id, **body):
    return test_client.post(
        "/v1/auth/tokens",
        json={"company_id": company_id, "name": "ci", **body},
        headers=headers,
    )


def test_a_token_acts_with_its_role_only(
    test_client, sqlite_session, signing_key, monkeypatch
):
    monkeypatch.setattr(settings, "AUTH_REQUIRED", True)
    monkeypatch.setattr(auth_tokens, "_configured_key", lambda: signing_key)
    company = _company(sqlite_session)
    manager = _headers(sqlite_session, company.id, "Manager")

    viewer_token = _mint(test_client, manager, company.id)  # Viewer by default
    assert viewer_token.status_code == 200
    body = viewer_token.json()
    assert body["role"] == "Viewer" and body["expires_at"] and not body["expired"]
    as_viewer = {"Authorization": f"Bearer {body['token']}"}
    # A Viewer token reads, and cannot do what only a Manager does.
    assert (
        test_client.get(
            f"/v1/auth/tokens?company_id={company.id}", headers=as_viewer
        ).status_code
        == 200
    )
    assert _mint(test_client, as_viewer, company.id).status_code == 403

    manager_token = _mint(test_client, manager, company.id, role="Manager").json()
    as_token = {"Authorization": f"Bearer {manager_token['token']}"}
    # A token never mints tokens, whatever its role.
    refused = _mint(test_client, as_token, company.id)
    assert refused.status_code == 403
    assert "cannot create API tokens" in refused.json()["detail"]
    listed = test_client.get(
        f"/v1/auth/tokens?company_id={company.id}", headers=manager
    ).json()["tokens"]
    assert {t["role"] for t in listed} == {"Viewer", "Manager"}
    assert all("token" not in t and "token_hash" not in t for t in listed)


def test_no_one_mints_a_token_above_their_role_or_an_owner_token(
    test_client, sqlite_session, signing_key, monkeypatch
):
    monkeypatch.setattr(settings, "AUTH_REQUIRED", True)
    monkeypatch.setattr(auth_tokens, "_configured_key", lambda: signing_key)
    company = _company(sqlite_session)
    viewer = _headers(sqlite_session, company.id, "Viewer")
    owner = _headers(sqlite_session, company.id, "Owner")
    assert _mint(test_client, viewer, company.id).status_code == 403
    assert _mint(test_client, owner, company.id, role="Owner").status_code == 422
    assert _mint(test_client, owner, company.id, role="Manager").status_code == 200
    assert _mint(test_client, owner, company.id, expires_in_days=0).status_code == 422
    assert _mint(test_client, owner, company.id, expires_in_days=366).status_code == 422
    forever = _mint(test_client, owner, company.id, expires_in_days=None).json()
    assert forever["expires_at"] is None


def test_an_expired_token_is_refused(
    test_client, sqlite_session, signing_key, monkeypatch
):
    from datetime import UTC, datetime, timedelta

    monkeypatch.setattr(settings, "AUTH_REQUIRED", True)
    monkeypatch.setattr(auth_tokens, "_configured_key", lambda: signing_key)
    company = _company(sqlite_session)
    token, raw = api_tokens.create_token(
        sqlite_session, company.id, "ci", role="Viewer", expires_in_days=1
    )
    assert api_tokens.verify(sqlite_session, raw) is not None
    token.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    sqlite_session.add(token)
    sqlite_session.commit()
    assert api_tokens.verify(sqlite_session, raw) is None
    response = test_client.get(
        f"/v1/auth/tokens?company_id={company.id}",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert response.status_code == 401
    # A token from before expiries never expires, and keeps acting as a Manager.
    legacy, legacy_raw = api_tokens.create_token(
        sqlite_session, company.id, "old", role="Manager", expires_in_days=None
    )
    assert legacy.expires_at is None
    assert api_tokens.verify(sqlite_session, legacy_raw) is not None


def test_an_unknown_role_is_refused_by_the_service(sqlite_session):
    company = _company(sqlite_session)
    with pytest.raises(ValueError):
        api_tokens.create_token(sqlite_session, company.id, "ci", role="Owner")


# A token never outranks its creator's current role (issue #512, TEAMS T5).
# The cap is checked on every use, so a demoted creator's token above their
# new role, or the token of a creator who left, is refused with an audit row.


def _member(session, company_id, role):
    user = User(
        email=f"c-{secrets.token_hex(4)}@x.com", full_name="c", password_hash="x"
    )
    session.add(user)
    session.commit()
    member = CompanyMember(company_id=company_id, user_id=user.id, human_role=role)
    session.add(member)
    session.commit()
    return user, member


def _set_role(session, member, role):
    member.human_role = role
    session.add(member)
    session.commit()


def _refusals(session, token_id):
    return list(
        session.exec(
            select(AuditLog).where(
                AuditLog.action == "api_token_refused",
                AuditLog.interaction_id == f"api-token:{token_id}",
            )
        ).all()
    )


def test_the_cap_is_pure():
    assert api_tokens.refusal("Manager", "Manager") is None
    assert api_tokens.refusal("Manager", "Owner") is None
    assert api_tokens.refusal("Viewer", "Viewer") is None
    assert api_tokens.refusal("Manager", "Viewer") == "creator_role_below_token"
    assert api_tokens.refusal("Viewer", None) == "creator_not_a_member"
    assert api_tokens.refusal("Viewer", "Stranger") == "creator_not_a_member"


def test_a_demoted_creators_token_above_the_new_role_is_refused_and_audited(
    sqlite_session,
):
    company = _company(sqlite_session)
    user, member = _member(sqlite_session, company.id, "Manager")
    manager_token, manager_raw = api_tokens.create_token(
        sqlite_session, company.id, "ci", user.email, role="Manager"
    )
    viewer_token, viewer_raw = api_tokens.create_token(
        sqlite_session, company.id, "read", user.email, role="Viewer"
    )
    assert api_tokens.verify(sqlite_session, manager_raw) is not None
    used_at = manager_token.last_used_at

    _set_role(sqlite_session, member, "Viewer")
    assert api_tokens.verify(sqlite_session, manager_raw) is None
    # A token at or below the new role keeps working.
    assert api_tokens.verify(sqlite_session, viewer_raw) is not None
    sqlite_session.refresh(manager_token)
    assert manager_token.last_used_at == used_at  # a refused use is not a use

    rows = _refusals(sqlite_session, manager_token.id)
    assert len(rows) == 1
    row = rows[0]
    assert row.company_id == company.id
    assert row.agent_id == f"api-token:{manager_token.id}"
    assert row.result["reason"] == "creator_role_below_token"
    assert row.details["token_role"] == "Manager"
    assert row.details["creator"] == user.email
    assert row.details["creator_role"] == "Viewer"
    assert _refusals(sqlite_session, viewer_token.id) == []

    # Refused again within the hour: still refused, one audit row, not one per call.
    assert api_tokens.verify(sqlite_session, manager_raw) is None
    assert len(_refusals(sqlite_session, manager_token.id)) == 1

    # Promoted again, the token is within its creator's role and works.
    _set_role(sqlite_session, member, "Manager")
    assert api_tokens.verify(sqlite_session, manager_raw) is not None


def test_a_removed_creators_tokens_stop_working(sqlite_session):
    company = _company(sqlite_session)
    user, member = _member(sqlite_session, company.id, "Manager")
    token, raw = api_tokens.create_token(
        sqlite_session, company.id, "read", user.email, role="Viewer"
    )
    assert api_tokens.verify(sqlite_session, raw) is not None

    sqlite_session.delete(member)
    sqlite_session.commit()
    assert api_tokens.verify(sqlite_session, raw) is None
    # The request ends in a 401 and its session closes without a commit: the
    # refusal must already be committed, or it is rolled back with the request.
    sqlite_session.rollback()
    rows = _refusals(sqlite_session, token.id)
    assert [r.result["reason"] for r in rows] == ["creator_not_a_member"]
    assert rows[0].details["creator_role"] is None


def test_a_token_whose_creator_account_is_gone_stops_working(sqlite_session):
    company = _company(sqlite_session)
    token, raw = api_tokens.create_token(
        sqlite_session, company.id, "read", "gone@x.com", role="Viewer"
    )
    assert api_tokens.verify(sqlite_session, raw) is None
    assert [r.result["reason"] for r in _refusals(sqlite_session, token.id)] == [
        "creator_not_a_member"
    ]


def test_a_creator_in_another_company_only_does_not_carry_the_token(sqlite_session):
    company, other = _company(sqlite_session), _company(sqlite_session)
    user, _ = _member(sqlite_session, other.id, "Owner")
    _token, raw = api_tokens.create_token(
        sqlite_session, company.id, "ci", user.email, role="Viewer"
    )
    assert api_tokens.verify(sqlite_session, raw) is None


def test_a_token_equal_to_its_creators_role_works(sqlite_session):
    company = _company(sqlite_session)
    for role in ("Viewer", "Manager"):
        user, _ = _member(sqlite_session, company.id, role)
        token, raw = api_tokens.create_token(
            sqlite_session, company.id, "ci", user.email, role=role
        )
        assert api_tokens.verify(sqlite_session, raw) is not None
        assert _refusals(sqlite_session, token.id) == []


def test_an_owners_token_works_until_the_owner_falls_below_it(sqlite_session):
    company = _company(sqlite_session)
    user, member = _member(sqlite_session, company.id, "Owner")
    token, raw = api_tokens.create_token(
        sqlite_session, company.id, "ci", user.email.upper(), role="Manager"
    )
    assert api_tokens.verify(sqlite_session, raw) is not None
    _set_role(sqlite_session, member, "Manager")  # still at the token's role
    assert api_tokens.verify(sqlite_session, raw) is not None
    _set_role(sqlite_session, member, "Viewer")
    assert api_tokens.verify(sqlite_session, raw) is None
    assert [r.result["reason"] for r in _refusals(sqlite_session, token.id)] == [
        "creator_role_below_token"
    ]


def test_a_token_minted_through_a_team_role_ends_with_the_team_membership(
    sqlite_session,
):
    company = _company(sqlite_session)
    user, _ = _member(sqlite_session, company.id, "Viewer")
    team = Team(company_id=company.id, name="ops", role="Manager")
    sqlite_session.add(team)
    sqlite_session.commit()
    seat = TeamMember(company_id=company.id, team_id=team.id, user_id=user.id)
    sqlite_session.add(seat)
    sqlite_session.commit()
    _token, raw = api_tokens.create_token(
        sqlite_session, company.id, "ci", user.email, role="Manager"
    )
    assert api_tokens.verify(sqlite_session, raw) is not None
    sqlite_session.delete(seat)
    sqlite_session.commit()
    assert api_tokens.verify(sqlite_session, raw) is None


def test_a_demoted_creators_token_is_a_401_over_http(
    test_client, sqlite_session, signing_key, monkeypatch
):
    monkeypatch.setattr(settings, "AUTH_REQUIRED", True)
    monkeypatch.setattr(auth_tokens, "_configured_key", lambda: signing_key)
    company = _company(sqlite_session)
    user, member = _member(sqlite_session, company.id, "Manager")
    login = {
        "Authorization": f"Bearer {create_access_token(user_id=user.id, email=user.email)}"
    }
    minted = _mint(test_client, login, company.id, role="Manager").json()
    as_token = {"Authorization": f"Bearer {minted['token']}"}
    listing = f"/v1/auth/tokens?company_id={company.id}"
    assert test_client.get(listing, headers=as_token).status_code == 200

    _set_role(sqlite_session, member, "Viewer")
    refused = test_client.get(listing, headers=as_token)
    assert refused.status_code == 401
    # The answer does not tell the holder what changed about the creator.
    assert refused.json()["detail"] == "Invalid or expired token"
    # And the demoted creator cannot mint a Manager token any more.
    assert _mint(test_client, login, company.id, role="Manager").status_code == 403


# The creator is recorded by account id, not only by email (issue #512, the
# SDL review of PR #644): an address can change hands, an account cannot.


def _rename(session, user, email):
    user.email = email
    session.add(user)
    session.commit()


def _mint_for(session, company_id, user, role):
    return api_tokens.create_token(
        session,
        company_id,
        "ci",
        user.email,
        role=role,
        created_by_user_id=user.id,
    )


def test_a_renamed_creator_keeps_the_tokens_standing(sqlite_session):
    company = _company(sqlite_session)
    user, member = _member(sqlite_session, company.id, "Manager")
    token, raw = _mint_for(sqlite_session, company.id, user, "Manager")
    assert token.created_by_user_id == user.id

    _rename(sqlite_session, user, f"renamed-{secrets.token_hex(4)}@x.com")
    assert api_tokens.verify(sqlite_session, raw) is not None
    assert _refusals(sqlite_session, token.id) == []
    # Still tied to the same account: demoted, the token is refused.
    _set_role(sqlite_session, member, "Viewer")
    assert api_tokens.verify(sqlite_session, raw) is None


def test_a_reused_email_does_not_inherit_a_token(sqlite_session):
    company = _company(sqlite_session)
    first, first_seat = _member(sqlite_session, company.id, "Manager")
    token, raw = _mint_for(sqlite_session, company.id, first, "Manager")
    address = first.email

    # The creator moves to another address, and someone else, a Manager of
    # the same company, takes the old one.
    _rename(sqlite_session, first, f"moved-{secrets.token_hex(4)}@x.com")
    second, _ = _member(sqlite_session, company.id, "Manager")
    _rename(sqlite_session, second, address)

    # The creator leaves: the token goes with them, not to the address.
    sqlite_session.delete(first_seat)
    sqlite_session.commit()
    assert api_tokens.verify(sqlite_session, raw) is None
    rows = _refusals(sqlite_session, token.id)
    assert [r.result["reason"] for r in rows] == ["creator_not_a_member"]
    assert rows[0].details["creator_user_id"] == first.id


def test_a_creator_id_with_no_account_is_refused_without_reading_the_email(
    sqlite_session,
):
    company = _company(sqlite_session)
    holder, _ = _member(sqlite_session, company.id, "Manager")
    _token, raw = api_tokens.create_token(
        sqlite_session,
        company.id,
        "ci",
        holder.email,
        role="Viewer",
        created_by_user_id=10_000_000,
    )
    assert api_tokens.verify(sqlite_session, raw) is None


def test_a_token_with_no_creator_id_keeps_the_email_rule(sqlite_session):
    # Rows the migration could not match (no account had the address then)
    # are judged by email, as before.
    company = _company(sqlite_session)
    user, member = _member(sqlite_session, company.id, "Manager")
    token, raw = api_tokens.create_token(
        sqlite_session, company.id, "old", user.email, role="Manager"
    )
    assert token.created_by_user_id is None
    assert api_tokens.verify(sqlite_session, raw) is not None
    _set_role(sqlite_session, member, "Viewer")
    assert api_tokens.verify(sqlite_session, raw) is None


def test_minting_over_http_records_the_creators_id(
    test_client, sqlite_session, signing_key, monkeypatch
):
    from models import ApiToken

    monkeypatch.setattr(settings, "AUTH_REQUIRED", True)
    monkeypatch.setattr(auth_tokens, "_configured_key", lambda: signing_key)
    company = _company(sqlite_session)
    user, _ = _member(sqlite_session, company.id, "Manager")
    login = {
        "Authorization": f"Bearer {create_access_token(user_id=user.id, email=user.email)}"
    }
    minted = _mint(test_client, login, company.id).json()
    sqlite_session.expire_all()
    assert sqlite_session.get(ApiToken, minted["id"]).created_by_user_id == user.id


def test_the_migration_backfills_the_creator_id_from_the_email(tmp_path, monkeypatch):
    import migrations
    from sqlalchemy import text
    from sqlmodel import create_engine

    from alembic import command

    url = f"sqlite:///{tmp_path / 'tokens.sqlite'}"
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", url)
    engine = create_engine(url)
    config = migrations.alembic_config()
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0047")
    with engine.begin() as connection:
        # In SQL: the model has columns later migrations add (0053).
        connection.execute(
            text(
                "INSERT INTO companies (company_name, default_scope_size, "
                "autonomy_level, git_workflow) VALUES ('Acme', 'M', 2, 1)"
            )
        )
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users (email, full_name, password_hash) "
                "VALUES ('ana@example.com', 'x', '')"
            )
        )
        ana = connection.execute(text("SELECT id FROM users")).scalar_one()
        for name, creator in (
            ("matched", "ana@example.com"),
            ("spelled", " Ana@Example.com"),
            ("unmatched", "gone@example.com"),
            ("anonymous", None),
        ):
            connection.execute(
                text(
                    "INSERT INTO api_tokens (company_id, name, token_hash, "
                    "created_by, scopes, created_at, role) VALUES "
                    "(1, :n, :h, :c, '[]', CURRENT_TIMESTAMP, 'Viewer')"
                ),
                {"n": name, "h": "h-" + name, "c": creator},
            )
    migrations.upgrade_to_head(engine)
    with engine.connect() as connection:
        ids = dict(
            connection.execute(
                text("SELECT name, created_by_user_id FROM api_tokens")
            ).fetchall()
        )
    assert ids == {"matched": ana, "spelled": ana, "unmatched": None, "anonymous": None}
    assert migrations.schema_differences(engine) == []
