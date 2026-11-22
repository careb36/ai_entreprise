"""The fixture data and request builder of the authorization matrix
(``test_authorization_matrix.py``, issue #541).

``build_world`` makes two companies: A, with one member per role (Viewer,
Manager, Owner) plus a bystander Viewer, and one row of every resource a
route can name (a sprint and a task, a run, a token, a specification and an
architecture, a procedure, a note, an MCP server, a provider connection...);
and B, whose Owner is the outsider. Every caller gets a Bearer access token.

``request_for`` turns an OpenAPI operation into a concrete request against
that world: path and query parameters and body fields are filled by name
(``company_id`` is A's, ``sprint_id`` A's sprint), the rest of the body
from the operation's JSON schema, with the declaration's ``body`` overriding
what a schema cannot say (a pattern, a URL that must be https).
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass, field
from typing import Any

import auth_tokens
from models import (
    Agent,
    AgentRun,
    BacklogItem,
    BusinessUnit,
    Company,
    CompanyMember,
    Routine,
    SavedView,
    Sprint,
    TaskRelation,
    User,
)
from models.api_token import ApiToken
from models.architecture import Architecture
from models.board_note import SprintBoardNote
from models.documentation import DocumentationPack
from models.finding import Finding
from models.intake import IntakeItem
from models.integration import SprintIntegration
from models.integration_review import IntegrationReviewComment
from models.manager import ManagerDecision
from models.mcp import McpServer
from models.procedure import Procedure
from models.provider import ProviderConnection
from models.review import ReviewComment, RunPublication
from models.rsi_cycle import RsiCycle
from models.run_secret import RunSecret
from models.specification import Specification
from models.wiki import WikiPage, WikiPageVersion

ROLES = ("Viewer", "Manager", "Owner")


@dataclass
class World:
    company_id: int
    other_company_id: int
    ids: dict[str, Any]
    headers: dict[str, dict[str, str]] = field(default_factory=dict)


def _user(session, label: str) -> tuple[User, dict[str, str]]:
    user = User(
        email=f"authz-{label.lower()}-{secrets.token_hex(5)}@example.com",
        full_name=f"Authz {label}",
        password_hash="unused",
    )
    session.add(user)
    session.commit()
    token = auth_tokens.create_access_token(user_id=user.id, email=user.email)
    return user, {"Authorization": f"Bearer {token}"}


def _add(session, row):
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def build_world(session) -> World:
    tag = secrets.token_hex(3)
    company = _add(session, Company(company_name=f"Authz A {tag}"))
    other = _add(session, Company(company_name=f"Authz B {tag}"))
    headers: dict[str, dict[str, str]] = {}
    users: dict[str, User] = {}
    for role in ROLES:
        user, header = _user(session, role)
        session.add(
            CompanyMember(company_id=company.id, user_id=user.id, human_role=role)
        )
        users[role], headers[role] = user, header
    bystander, _ = _user(session, "bystander")
    session.add(
        CompanyMember(company_id=company.id, user_id=bystander.id, human_role="Viewer")
    )
    outsider, headers["outsider"] = _user(session, "outsider")
    session.add(
        CompanyMember(company_id=other.id, user_id=outsider.id, human_role="Owner")
    )
    session.commit()

    cid = company.id
    unit = _add(session, BusinessUnit(company_id=cid, unit_name="Authz unit"))
    agent = _add(session, Agent(company_id=cid, name="Authz", role="Developer"))
    spec = _add(session, Specification(company_id=cid, title="Authz spec"))
    arch = _add(
        session,
        Architecture(company_id=cid, specification_id=spec.id, title="Authz arch"),
    )
    sprint = _add(
        session,
        Sprint(company_id=cid, sprint_name="Authz sprint", business_unit_id=unit.id),
    )
    item = _add(session, BacklogItem(sprint_id=sprint.id, title="Authz task"))
    other = _add(session, BacklogItem(sprint_id=sprint.id, title="Authz other task"))
    routine = _add(
        session,
        Routine(
            company_id=cid,
            title="Authz routine",
            assignee="Developer",
            schedule={"every": "day", "at": "09:00", "timezone": "UTC"},
        ),
    )
    relation = _add(
        session,
        TaskRelation(
            company_id=cid,
            task_id=item.id,
            related_task_id=other.id,
            pair_low=min(item.id, other.id),
            pair_high=max(item.id, other.id),
            kind="relates_to",
            proposed_by="Authz",
            proposed_role="person",
        ),
    )
    # A company view written by the Viewer, so each role may read and change
    # it (a view's author changes it; a Manager changes any company view).
    view = _add(
        session,
        SavedView(
            company_id=cid,
            name="Authz view",
            access="company",
            owner_user_id=users["Viewer"].id,
            owner_name=users["Viewer"].email,
        ),
    )
    intake = _add(
        session,
        IntakeItem(
            company_id=cid, source="brief", title="Authz request", submitted_by="Authz"
        ),
    )
    run = _add(
        session,
        AgentRun(
            company_id=cid,
            external_ref=f"authz:{tag}",
            producer="test",
            artifact_digest="unused",
            artifact_size=0,
            subject={"sprint_id": sprint.id, "backlog_item_id": item.id},
        ),
    )
    token = _add(
        session,
        ApiToken(
            company_id=cid,
            name="Authz token",
            token_hash=hashlib.sha256(secrets.token_bytes(16)).hexdigest(),
            role="Viewer",
        ),
    )
    decision = _add(
        session,
        ManagerDecision(
            company_id=cid,
            item_kind="hold",
            item_id=item.id,
            item_state="held",
            category="hold",
        ),
    )
    note = _add(
        session,
        SprintBoardNote(
            company_id=cid,
            sprint_id=sprint.id,
            kind="note",
            text="Authz note",
            author_role="ScrumMaster",
        ),
    )
    # The sprint's integration and a comment on it (the integration's reviews).
    integrated = _add(
        session,
        SprintIntegration(
            company_id=cid,
            sprint_id=sprint.id,
            trigger="manual",
            requested_by="human:Authz",
            status="untested",
            branch=f"fabrique/sprint-{sprint.id}",
            base="main",
            repository="/authz/repository",
            commit="0" * 40,
        ),
    )
    integration_comment = _add(
        session,
        IntegrationReviewComment(
            company_id=cid,
            sprint_id=sprint.id,
            integration_id=integrated.id,
            body="Authz",
            commit="0" * 40,
            author_name="Authz",
        ),
    )
    procedure = _add(
        session,
        Procedure(
            company_id=cid,
            role="Developer",
            title="Authz procedure",
            body="Do it.",
            proposed_by="ScrumMaster",
        ),
    )
    finding = _add(
        session,
        Finding(
            company_id=cid,
            source="docs/retrospectives/2026-11-self-hosting.md",
            source_ref="F5",
            title="Authz finding",
            specification_sha256=hashlib.sha256(b"authz specification").hexdigest(),
            target_measure="scripts/self_host/measures/test_authz.py",
        ),
    )
    rsi = _add(
        session,
        RsiCycle(
            company_id=cid,
            cycle_key="cycle-authz",
            finding_id=finding.id,
            baseline_sha="b" * 40,
        ),
    )
    comment = _add(
        session,
        ReviewComment(
            company_id=cid, agent_run_id=run.id, body="Authz", author_name="Authz"
        ),
    )
    publication = _add(
        session,
        RunPublication(
            company_id=cid,
            agent_run_id=run.id,
            branch="authz",
            base="main",
            commit="0" * 40,
            how="compare",
            published_by="Authz",
        ),
    )
    secret = _add(
        session,
        RunSecret(
            company_id=cid,
            task_id=item.id,
            name="AUTHZ_SECRET",
            encrypted_value="plaintext:unused",
        ),
    )
    server = _add(
        session, McpServer(company_id=cid, name="authz", transport="streamable_http")
    )
    provider = _add(
        session,
        ProviderConnection(
            company_id=cid,
            provider="openai_compatible",
            name="authz",
            base_url="https://llm.example.com/v1",
        ),
    )
    pack = _add(
        session,
        DocumentationPack(
            company_id=cid,
            documents=[{"key": "overview", "title": "Overview", "markdown": "# A"}],
        ),
    )
    page = _add(
        session, WikiPage(company_id=cid, slug="authz", kind="concept", title="Authz")
    )
    version = _add(
        session,
        WikiPageVersion(
            company_id=cid,
            page_id=page.id,
            number=1,
            author_kind="agent",
            author="TechWriter",
            title="Authz",
            body="Authz page.",
            status="proposed",
        ),
    )
    ids = {
        "company_id": cid,
        "pack_id": pack.id,
        "key": "overview",
        "slug": page.slug,
        "version_id": version.id,
        "bu_id": unit.id,
        "business_unit_id": unit.id,
        "agent_id": agent.id,
        "routine_id": routine.id,
        "sprint_id": sprint.id,
        "task_id": item.id,
        "relation_id": relation.id,
        "view_id": view.id,
        "item_id": item.id,
        "intake_id": intake.id,
        "of_task_id": item.id,
        "backlog_item_id": item.id,
        "run_id": run.id,
        "agent_run_id": run.id,
        "token_id": token.id,
        "decision_id": decision.id,
        "note_id": note.id,
        "procedure_id": procedure.id,
        "finding_id": finding.id,
        "rsi_cycle_id": rsi.id,
        "comment_id": comment.id,
        "integration_id": integrated.id,
        "integration_comment_id": integration_comment.id,
        "publication_id": publication.id,
        "secret_id": secret.id,
        "spec_id": spec.id,
        "specification_id": spec.id,
        "arch_id": arch.id,
        "version": arch.version,
        "server_id": server.id,
        "provider_id": provider.id,
        "user_id": bystander.id,
        "index": 0,
        "provider": "slack",
    }
    return World(company_id=cid, other_company_id=other.id, ids=ids, headers=headers)


# -- requests from the OpenAPI document ------------------------------------


def _resolve(schema: dict, components: dict) -> dict:
    while "$ref" in schema:
        schema = components[schema["$ref"].split("/")[-1]]
    return schema


def sample(schema: dict, components: dict, name: str, ids: dict) -> Any:
    """A value that satisfies *schema* (as far as a schema says), with ids
    taken from the world by field name."""
    schema = _resolve(schema, components)
    if name in ids and name not in {"provider", "index", "key", "slug"}:
        kinds = {schema.get("type")} | {
            _resolve(s, components).get("type")
            for s in schema.get("anyOf", []) + schema.get("oneOf", [])
        }
        return ids[name] if "integer" in kinds else str(ids[name])
    for key in ("anyOf", "oneOf"):
        if key in schema:
            options = [
                s for s in schema[key] if _resolve(s, components).get("type") != "null"
            ]
            return sample(options[0], components, name, ids) if options else None
    if "allOf" in schema:
        merged: dict = {}
        for part in schema["allOf"]:
            merged.update(_resolve(part, components))
        return sample(merged, components, name, ids)
    if "const" in schema:
        return schema["const"]
    if "enum" in schema:
        return schema["enum"][0]
    if "default" in schema and schema["default"] is not None:
        return schema["default"]
    kind = schema.get("type")
    if kind == "object" or "properties" in schema:
        required = set(schema.get("required", []))
        out = {}
        for prop, sub in schema.get("properties", {}).items():
            if prop in required or prop in ids:
                out[prop] = sample(sub, components, prop, ids)
        return out
    if kind == "array":
        count = int(schema.get("minItems", 0))
        return [
            sample(schema.get("items", {}), components, name, ids) for _ in range(count)
        ]
    if kind == "integer":
        return max(1, int(schema.get("minimum", 1)))
    if kind == "number":
        return float(max(1, schema.get("minimum", 1)))
    if kind == "boolean":
        return False
    if kind == "string":
        if "email" in name:
            return "authz-target@example.com"
        if "url" in name:
            return "https://hooks.example.com/authz"
        length = max(int(schema.get("minLength", 1)), 1)
        return ("authz" * (length // 5 + 1))[: max(length, 5)]
    return "authz"


@dataclass
class Call:
    method: str
    path: str
    params: dict[str, Any]
    json: Any


def request_for(
    method: str,
    path: str,
    operation: dict,
    components: dict,
    world: World,
    declared: dict,
) -> Call:
    ids = world.ids
    concrete = path
    params: dict[str, Any] = {}
    for parameter in operation.get("parameters", []):
        name = parameter["name"]
        value = ids.get(name, None)
        if parameter["in"] == "path":
            concrete = concrete.replace(
                "{" + name + "}", str(value if value is not None else 1)
            )
        elif parameter["in"] == "query" and (parameter.get("required") or name in ids):
            params[name] = value if value is not None else "authz"
    params.update(declared.get("query", {}))
    body = None
    content = (operation.get("requestBody") or {}).get("content", {})
    if "application/json" in content:
        body = sample(content["application/json"]["schema"], components, "", ids)
        if isinstance(body, dict):
            body.update(declared.get("body", {}))
    return Call(method=method.upper(), path=concrete, params=params, json=body)
