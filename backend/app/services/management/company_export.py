"""A company's configuration as one file, without its secrets (issue #774).

``export`` gathers what defines how the company's agents work, so an Owner
can keep it, review it or start another company from it:

- the company's settings (an explicit list, ``COMPANY_FIELDS``: a column
  added later is not exported until someone adds it here);
- its agents (name, role, persona, seniority, specialty, model, prompt,
  budget), business units, routines and model provider connections.

What is never in it:

- **secrets.** A provider's API key and headers, a project credential's
  value: each is left out and named in ``required_secrets`` instead, so
  whoever uses the file knows what to supply. The sandbox and console
  environment are already names only. Every string is then read line by line
  with the Verifier's secret detectors (``evidence/standards``): a line that
  looks like a key, a token or an assigned password is replaced, and its
  place is listed in ``redacted`` (where, never what). A URL's user and
  password are dropped;
- people: members, emails, users; and history: sprints, tasks, runs,
  audit rows. The file is configuration, not a backup.

``manifest.sha256`` is the backend's own digest of the exported content (its
canonical JSON), computed here, so a copy can be checked against the audit
row ``company_exported`` that records it. The design follows Paperclip's
company portability export (MIT: ``server/src/services/company-portability.ts``:
secrets scrubbed and replaced by a list of the ones required); no code taken.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from models import Agent, BusinessUnit, Company, ProjectCredential, Routine
from models.provider import ProviderConnection
from sqlmodel import Session, select

from services.evidence import standards
from services.platform.agent_service import AgentService

FORMAT = "la-fabrique.company-export/1"
AUDIT_ACTION = "company_exported"
REDACTED_LINE = "[line redacted: it looks like a secret]"

COMPANY_FIELDS = (
    "company_name",
    "default_scope_size",
    "autonomy_level",
    "team_selection",
    "git_workflow",
    "git_base_branch",
    "research_hosts",
    "delivery_sandbox",
    "sandbox_env",
    "stack_profile",
    "default_model",
    "role_models",
    "model_policy",
    "console_producers",
    "console_env",
    "doc_language",
    "integration_review_required",
    "protected_paths_enabled",
    "forge_policy",
    "run_window",
    "limits",
)
AGENT_FIELDS = (
    "name",
    "role",
    "persona",
    "seniority",
    "specialty",
    "model",
    "system_prompt",
    "token_budget",
)
ROUTINE_FIELDS = (
    "title",
    "description",
    "assignee",
    "schedule",
    "enabled",
    "variables",
)


def _without_userinfo(url: str) -> str:
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    if not (parts.username or parts.password):
        return url
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    return urlunsplit((parts.scheme, host, parts.path, parts.query, parts.fragment))


_URL = re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s'\"<>]+", re.IGNORECASE)


def _scrub_text(text: str, where: str, redacted: list[dict[str, Any]]) -> str:
    cleaned = _URL.sub(lambda m: _without_userinfo(m.group(0)), text)
    if cleaned != text:
        redacted.append({"at": where, "what": "credentials in a URL"})
    lines = cleaned.split("\n")
    count = 0
    for index, line in enumerate(lines):
        if standards.secrets_in("export", line) or standards.secret_line(line):
            lines[index] = REDACTED_LINE
            count += 1
    if count:
        redacted.append({"at": where, "what": "secret-looking line(s)", "lines": count})
    return "\n".join(lines)


def scrub(value: Any, where: str, redacted: list[dict[str, Any]]) -> Any:
    """*value* with every string scrubbed (``_scrub_text``), recursively;
    each change listed in *redacted* by where it was."""
    if isinstance(value, str):
        return _scrub_text(value, where, redacted)
    if isinstance(value, dict):
        return {k: scrub(v, f"{where}.{k}", redacted) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub(v, f"{where}[{i}]", redacted) for i, v in enumerate(value)]
    return value


def digest(content: dict[str, Any]) -> str:
    canonical = json.dumps(content, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _content(session: Session, company: Company) -> tuple[dict[str, Any], list[dict]]:
    cid = company.id
    agents = session.exec(
        select(Agent).where(Agent.company_id == cid).order_by(Agent.id)
    ).all()
    units = session.exec(
        select(BusinessUnit)
        .where(BusinessUnit.company_id == cid)
        .order_by(BusinessUnit.id)
    ).all()
    routines = session.exec(
        select(Routine).where(Routine.company_id == cid).order_by(Routine.id)
    ).all()
    providers = session.exec(
        select(ProviderConnection)
        .where(ProviderConnection.company_id == cid)
        .order_by(ProviderConnection.id)
    ).all()
    credentials = session.exec(
        select(ProjectCredential).where(ProjectCredential.company_id == cid)
    ).all()
    required: list[dict[str, Any]] = [
        {"kind": "provider_api_key", "connection": p.name, "provider": p.provider}
        for p in providers
        if p.api_key
    ]
    required += [
        {"kind": "provider_headers", "connection": p.name, "provider": p.provider}
        for p in providers
        if p.headers
    ]
    required += [
        {
            "kind": "project_credential",
            "type": c.credential_type,
            "access": c.access_level,
        }
        for c in credentials
    ]
    required += [
        {"kind": "environment_variable", "name": str(name), "for": scope}
        for scope, names in (
            ("sandbox", company.sandbox_env),
            ("console", company.console_env),
        )
        for name in names or []
    ]
    content = {
        "company": {f: getattr(company, f) for f in COMPANY_FIELDS},
        "agents": [{f: getattr(a, f) for f in AGENT_FIELDS} for a in agents],
        "business_units": [
            {"unit_name": u.unit_name, "token_quota_limit": u.token_quota_limit}
            for u in units
        ],
        "routines": [{f: getattr(r, f) for f in ROUTINE_FIELDS} for r in routines],
        "provider_connections": [
            {
                "provider": p.provider,
                "name": p.name,
                "base_url": p.base_url,
                "enabled": p.enabled,
            }
            for p in providers
        ],
    }
    return content, required


def export(
    session: Session, company_id: int, actor: dict[str, Any] | None
) -> dict[str, Any]:
    """The company's export, audited as ``company_exported``. Commits."""
    company = session.get(Company, company_id)
    if company is None:
        raise ValueError(f"Company {company_id} not found")
    raw, required = _content(session, company)
    redacted: list[dict[str, Any]] = []
    content = scrub(raw, "$", redacted)
    sha = digest(content)
    actor = actor or {}
    exported_at = datetime.now(UTC).isoformat()
    AgentService().audit_interaction(
        session=session,
        company_id=company_id,
        interaction_id=f"company:{company_id}:export",
        agent_id=str(actor.get("by") or "unknown"),
        action=AUDIT_ACTION,
        details={
            "company_id": str(company_id),
            "user_id": actor.get("user_id"),
            "token_id": actor.get("token_id"),
        },
        result={
            "sha256": sha,
            "format": FORMAT,
            "redacted": len(redacted),
            "required_secrets": len(required),
        },
        decision_signature=None,
        cost_tokens=0,
        commit=False,
    )
    session.commit()
    return {
        "manifest": {
            "format": FORMAT,
            "company_id": company_id,
            "exported_at": exported_at,
            "sha256": sha,
            "sha256_of": "content, canonical JSON (sorted keys, no spaces)",
        },
        "content": content,
        "required_secrets": required,
        "redacted": redacted,
    }
