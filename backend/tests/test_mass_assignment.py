"""A request body cannot carry a privileged field its model does not declare
(issue #541; WSTG-INJT-20 at latest@87cf4a4, mass assignment).

Every JSON request body of the API is a ``schemas.RequestModel``: an
undeclared ``company_id``, ``role``, ``human_role``, ``owner_id``,
``is_admin``... is refused with 422 instead of being silently dropped. The
sign-in and membership bodies refuse every unknown field. The first test
walks every operation of ``app.openapi()``, so a new body model that is not
a ``RequestModel`` fails here.
"""

from __future__ import annotations

import typing

import pytest
from authz_world import World, sample
from schemas import PRIVILEGED_FIELDS, RequestModel

from app.main import app

_OPENAPI = app.openapi()
_COMPONENTS = _OPENAPI["components"]["schemas"]
# Bodies that are not a model: a free-form map whose handler validates its
# own keys and never reads a company or a role from it.
FREE_FORM = {
    "PUT /v1/companies/{company_id}/manager_policy",
    "POST /v1/memory/store",
}
_WORLD = World(company_id=1, other_company_id=2, ids={"company_id": 1, "index": 0})


def _body_operations():
    for path, operations in _OPENAPI["paths"].items():
        for method, operation in operations.items():
            content = (operation.get("requestBody") or {}).get("content", {})
            if "application/json" not in content:
                continue
            key = f"{method.upper()} {path}"
            schema = content["application/json"]["schema"]
            yield key, method, path, schema


BODIES = sorted(_body_operations())


def _model_name(schema):
    """The model's name, also inside an optional body (``anyOf`` with null)."""
    for option in [schema, *schema.get("anyOf", [])]:
        if "$ref" in option:
            return option["$ref"].split("/")[-1]
    return ""


def _concrete(path):
    return (
        path.replace("{provider}", "slack").replace("{index}", "0").format_map(_Ones())
    )


class _Ones(dict):
    def __missing__(self, key):
        return "1"


def test_every_json_body_is_a_request_model():
    from fastapi.routing import APIRoute

    def routes(router):
        for route in getattr(router, "routes", []):
            if isinstance(route, APIRoute):
                yield route
            else:
                yield from routes(getattr(route, "original_router", route))

    offenders = []
    for route in routes(app.router):
        body = route.body_field
        if body is None:
            continue
        annotation = getattr(body.field_info, "annotation", None)
        options = typing.get_args(annotation) or (annotation,)
        models = [a for a in options if a is not type(None)]
        if all(isinstance(a, type) and issubclass(a, RequestModel) for a in models):
            continue
        key = f"{min(route.methods)} {route.path}"
        if key not in FREE_FORM:
            offenders.append(key)
    assert offenders == []


@pytest.mark.parametrize(
    "key, method, path, schema", BODIES, ids=[b[0] for b in BODIES]
)
def test_an_undeclared_privileged_field_is_refused(
    test_client, key, method, path, schema
):
    if key in FREE_FORM:
        pytest.skip("free-form map, validated by its handler")
    model = _COMPONENTS[_model_name(schema)]
    declared = set(model.get("properties", {}))
    undeclared = sorted(PRIVILEGED_FIELDS - declared)
    body = sample(_COMPONENTS[_model_name(schema)], _COMPONENTS, "", _WORLD.ids)
    for field in ("company_id", "role", "human_role", "owner_id", "is_admin"):
        if field not in undeclared:
            continue
        response = test_client.request(
            method.upper(), _concrete(path), json={**body, field: "Owner"}
        )
        assert response.status_code == 422, (field, response.text)
        assert field in response.text


@pytest.mark.parametrize(
    "path, body",
    [
        ("/v1/auth/login", {"email": "a@example.com", "password": "x", "remember": 1}),
        (
            "/v1/auth/register",
            {
                "email": "a@example.com",
                "full_name": "A",
                "password": "x",
                "plan": "pro",
            },
        ),
        (
            "/v1/company/invite",
            {"company_id": 1, "email": "a@example.com", "human_role": "Viewer", "x": 1},
        ),
        ("/v1/auth/sso/exchange", {"code": "abc", "next": "/"}),
        ("/v1/auth/tokens", {"company_id": 1, "name": "t", "expires_at": "never"}),
    ],
)
def test_the_sign_in_and_membership_bodies_refuse_any_unknown_field(
    test_client, path, body
):
    response = test_client.post(path, json=body)
    assert response.status_code == 422
    assert "extra_forbidden" in response.text


def test_a_declared_privileged_field_is_still_accepted():
    """``company_id`` where a route checks the caller's role in it, ``role``
    on an API token: declared, so validated like any other field."""
    from routers.api_tokens import CreateTokenRequest

    token = CreateTokenRequest(company_id=3, name="ci", role="Viewer")
    assert (token.company_id, token.role) == (3, "Viewer")


def test_an_unprivileged_unknown_field_is_still_ignored():
    """Clients that send an extra harmless field keep working."""
    from schemas import AgentUpdateRequest

    update = AgentUpdateRequest(name="Atlas", colour="blue")
    assert update.name == "Atlas" and not hasattr(update, "colour")
