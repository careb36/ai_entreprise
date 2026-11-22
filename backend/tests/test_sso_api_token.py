"""SSO refuses an API token (``routers/sso.py``): ``start`` and ``exchange``
answer 401 to a request whose ``Authorization`` header carries one, valid or
not, before any flow begins; a browser request without it is unchanged."""

from __future__ import annotations

import pytest
from models import SsoFlowState
from services.platform import api_tokens
from sqlmodel import select
from sso_fake_idp import company, connect

REFUSED = "API tokens cannot be used to sign in with SSO"


def _token(session, company_id):
    _, raw = api_tokens.create_token(session, company_id, "script")
    return raw


def test_start_refuses_a_valid_api_token(sqlite_session, test_client, fake_idp):
    conn = connect(sqlite_session, company(sqlite_session).id)
    raw = _token(sqlite_session, conn.company_id)
    response = test_client.get(
        f"/v1/auth/sso/{conn.slug}/start",
        headers={"Authorization": f"Bearer {raw}"},
        follow_redirects=False,
    )
    assert response.status_code == 401
    assert response.json()["detail"] == REFUSED
    assert sqlite_session.exec(select(SsoFlowState)).all() == []  # no flow begun


def test_exchange_refuses_an_api_token_even_a_made_up_one(
    sqlite_session, test_client, fake_idp
):
    response = test_client.post(
        "/v1/auth/sso/exchange",
        json={"code": "anything"},
        headers={"Authorization": "Bearer fab_not-a-real-token"},
    )
    assert response.status_code == 401
    assert response.json()["detail"] == REFUSED
    assert "fabrique_session" not in response.cookies


def test_a_browser_request_still_starts(sqlite_session, test_client, fake_idp):
    conn = connect(sqlite_session, company(sqlite_session).id)
    response = test_client.get(
        f"/v1/auth/sso/{conn.slug}/start", follow_redirects=False
    )
    assert response.status_code == 302


@pytest.mark.parametrize(
    ("header", "is_token"),
    [
        ("Bearer fab_abc", True),
        ("bearer  fab_abc", True),
        ("Bearer eyJhbGciOi.jwt", False),
        ("Basic fab_abc", False),
        ("", False),
        (None, False),
    ],
)
def test_the_header_check(header, is_token):
    assert api_tokens.bearer_is_token(header) is is_token
