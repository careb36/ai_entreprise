"""A company's SSO connection: presets, validation, what the API shows, the test.

One OpenID Connect mechanism; Google and Microsoft Entra ID are presets that
fill the issuer from a template, not separate code paths. The client secret
is encrypted with the Fernet key (``crypto.encrypt_value``) and the API says
only whether one is set. ``test`` fetches discovery and the JWKS and records
what it observed on the connection, as the provider connections do.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

import crypto
from models import SsoConnection

from services.platform import oidc

PRESETS: dict[str, dict[str, Any]] = {
    "generic": {"issuer": None, "needs_tenant": False},
    "google": {"issuer": "https://accounts.google.com", "needs_tenant": False},
    "entra": {
        "issuer": "https://login.microsoftonline.com/{tenant}/v2.0",
        "needs_tenant": True,
    },
}
MEMBER_ROLES = ("Viewer", "Manager")  # never "Owner" by provisioning
# Public mail providers are not a company's domain (after Flow's §7.3): naming
# one would let auto-join admit anyone with an account there.
PUBLIC_MAIL_DOMAINS = frozenset(
    {
        "gmail.com",
        "googlemail.com",
        "outlook.com",
        "hotmail.com",
        "live.com",
        "msn.com",
        "yahoo.com",
        "icloud.com",
        "me.com",
        "aol.com",
        "proton.me",
        "protonmail.com",
        "gmx.com",
        "mail.com",
        "yandex.com",
        "zoho.com",
    }
)
SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?$")
DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$"
)
TENANT_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
MAX_DOMAINS = 20
MAX_DETAIL_CHARS = 300


class SsoConfigError(ValueError):
    """The connection asked for cannot be saved; the message says why."""


def clean_slug(value: str | None) -> str:
    slug = (value or "").strip().lower()
    if not SLUG_RE.match(slug):
        raise SsoConfigError(
            "slug: 1 to 40 lowercase letters, digits or hyphens, not at either end"
        )
    return slug


def clean_domains(values: list[str] | None) -> list[str]:
    domains: list[str] = []
    for raw in values or []:
        domain = str(raw).strip().lower().lstrip("@")
        if not domain:
            continue
        if not DOMAIN_RE.match(domain):
            raise SsoConfigError(f"allowed_domains: {domain!r} is not a domain")
        if domain in PUBLIC_MAIL_DOMAINS:
            raise SsoConfigError(
                f"allowed_domains: {domain} is a public mail provider, not a company's domain"
            )
        if domain not in domains:
            domains.append(domain)
    if not domains:
        raise SsoConfigError("allowed_domains: name at least one domain")
    if len(domains) > MAX_DOMAINS:
        raise SsoConfigError(f"allowed_domains: at most {MAX_DOMAINS}")
    return domains


def clean_role(value: str | None) -> str:
    role = (value or "Viewer").strip()
    if role not in MEMBER_ROLES:
        raise SsoConfigError("default_role: Viewer or Manager (never Owner)")
    return role


def issuer_for(preset: str, issuer: str | None, tenant: str | None) -> str:
    """The issuer a preset stands for, or the generic one checked."""
    if preset not in PRESETS:
        raise SsoConfigError(f"preset: one of {', '.join(PRESETS)}")
    template = PRESETS[preset]["issuer"]
    if template is None:
        value = (issuer or "").strip().rstrip("/")
    else:
        if PRESETS[preset]["needs_tenant"]:
            tenant = (tenant or "").strip().lower()
            if not TENANT_RE.match(tenant):
                raise SsoConfigError(
                    "tenant: the directory (tenant) ID, a GUID; "
                    "multi-tenant issuers are not supported"
                )
        value = template.format(tenant=tenant)
    try:
        return oidc.check_url(value, field="issuer")
    except oidc.SsoError as exc:
        raise SsoConfigError(str(exc)) from exc


def set_secret(row: SsoConnection, secret: str | None) -> None:
    """``None`` keeps the stored secret; an empty string clears it."""
    if secret is None:
        return
    secret = secret.strip()
    row.client_secret = crypto.encrypt_value(secret) if secret else None


def client_secret(row: SsoConnection) -> str | None:
    """The stored secret; ``SsoError`` when it cannot be read (the Fernet key
    changed), so a sign-in is refused with a reason rather than a 500."""
    if not row.client_secret:
        return None
    try:
        return crypto.decrypt_value(row.client_secret)
    except crypto.CredentialCryptoError as exc:
        raise oidc.SsoError(
            "idp_misconfigured", "the client secret cannot be decrypted"
        ) from exc


def redirect_uri() -> str:
    from config import settings

    return settings.SSO_CALLBACK_URL


def serialize(row: SsoConnection) -> dict[str, Any]:
    """What the API shows: everything but the secret, which is a yes or no."""
    return {
        "id": row.id,
        "company_id": row.company_id,
        "slug": row.slug,
        "preset": row.preset,
        "tenant": row.tenant,
        "issuer": row.issuer,
        "client_id": row.client_id,
        "has_client_secret": bool(row.client_secret),
        "allowed_domains": list(row.allowed_domains or []),
        "default_role": row.default_role,
        "auto_join": row.auto_join,
        "enabled": row.enabled,
        "require_sso": row.require_sso,
        "groups_enabled": row.groups_enabled,
        "groups_claim": row.groups_claim,
        "group_role_cap": row.group_role_cap,
        "tested_at": row.tested_at.isoformat() if row.tested_at else None,
        "test_ok": row.test_ok,
        "test_detail": row.test_detail,
        "redirect_uri": redirect_uri(),
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def test(row: SsoConnection) -> dict[str, Any]:
    """Fetch discovery and the JWKS now; record and return what was observed."""
    observed: dict[str, Any] = {}
    try:
        doc = oidc.discover(row.issuer, fresh=True)
        observed = {
            "issuer": doc.get("issuer"),
            "authorization_endpoint": doc.get("authorization_endpoint"),
            "token_endpoint": doc.get("token_endpoint"),
            "jwks_uri": doc.get("jwks_uri"),
            "signing_algorithms": oidc.signing_algorithms(doc),
            "code_challenge_methods": doc.get("code_challenge_methods_supported"),
        }
        if not observed["signing_algorithms"]:
            raise oidc.SsoError(
                "idp_misconfigured", "no asymmetric signing algorithm in common"
            )
        keys = oidc.jwks(doc["jwks_uri"], fresh=True)["keys"]
        observed["key_count"] = len(keys)
        if not keys:
            raise oidc.SsoError("idp_misconfigured", "the JWKS holds no key")
        ok, detail = True, "discovery and JWKS fetched"
    except oidc.SsoError as exc:
        ok, detail = False, str(exc)[:MAX_DETAIL_CHARS]
    row.tested_at = datetime.now(UTC).replace(tzinfo=None)
    row.test_ok = ok
    row.test_detail = detail
    return {"ok": ok, "detail": detail, "observed": observed}
