"""Which OWASP WSTG tests apply to a project, decided by a fixed rule table (issue #539).

``plan`` reads the project's specification, architecture and detected stack
(the documentation pack's snapshot, ``project_docs.collect``) and returns one
row per test of the versioned catalogue (``services/platform/wstg_catalogue``):
applicable or not, and why. No model decides it: a **signal** (the project
has authentication, roles, an API, uploads, a database, outbound fetches,
payments, OAuth, SAML, GraphQL, WebSockets, JWT, a web surface or a browser
client) is a word or a field found in those records, cited by where it was
found; a **rule** (``RULES``) names the tests a signal (or two together,
``api+auth``) brings in. A test no
rule selects is not applicable, with the signals that would have brought it in.

Each applicable row is ``auto`` or ``manual``:

- ``auto`` / ``security_probe``: the daemon's security probe of the running
  application observes it (``services/evidence/security_probe.py``, #540);
- ``auto`` / ``qa_test``: QA can write a test for it, named
  ``test_wstg_<cat>_<nn>_…`` (``security_test_tasks``), which the daemon runs
  and ``services/evidence/wstg_tests.py`` reads back;
- ``manual``: it needs a person's judgement, a deployed https environment or a
  third party's configuration, so only a person's recorded review settles it.

A model may only **propose additions** (``proposals``): a proposed test that
no rule selected becomes an applicable ``manual`` row marked as a claim, with
who proposed it; a proposal can never remove a row or change a rule's row.

Pure; no I/O beyond the cached catalogue.
"""

from __future__ import annotations

import re
from typing import Any

from services.contracts.task_text import DESCRIPTION_MAX
from services.platform import wstg_catalogue

AUTO, MANUAL = "auto", "manual"
PROBE, QA_TEST, PERSON = "security_probe", "qa_test", "person"
QA_ROLE = "QA"
_WHERE_LIMIT = 3

# Words and fields that show a signal, in English and Spanish (a specification
# may be written in either). Matched case-insensitively on word boundaries.
SIGNAL_WORDS: dict[str, str] = {
    "web": r"web ?app|website|sitio web|web site|https?\b|browser|navegador|html|endpoint|rest api|api rest|frontend|front-end",
    "browser": r"browser|navegador|frontend|front-end|web client|cliente web|single.page|spa\b|react|vue|angular|svelte|next\.js|html|página web|web page",
    "auth": r"log ?in|sign ?in|sign ?up|password|passwords|authenticat\w*|credentials?|user accounts?|inici\w* (?:de )?sesión|contraseñas?|autentica\w*|registr\w* de usuarios?|mfa|2fa",
    "roles": r"roles?|admins?|administrators?|administrador\w*|permissions?|permisos?|authori[sz]ation|autoriza\w*|rbac|privileges?|privilegios?",
    "api": r"api|apis|rest api|restful|endpoints?|openapi|swagger",
    "uploads": r"uploads?|uploaded|attachments?|multipart|file import|subir archivos?|adjunt\w*|carga de archivos?",
    "database": r"database|databases|base de datos|bases de datos|sql|postgres\w*|mysql|mariadb|sqlite|mongo\w*|redis|orm",
    "outbound": r"webhooks?|callback url|fetch(?:es)? (?:a |the )?urls?|url preview|link preview|import from (?:a )?url|server-side request|descarga\w* desde (?:una )?url|vista previa de enlaces",
    "payments": r"payments?|checkout|stripe|paypal|billing|invoices?|credit card|card number|pagos?|facturaci\w*|facturas?|tarjeta de crédito",
    "oauth": r"oauth\w*|openid|oidc|sign in with|iniciar sesión con",
    "saml": r"saml",
    "graphql": r"graphql",
    "websockets": r"websockets?|web sockets?|socket\.io|server-sent events",
    "jwt": r"jwts?|json web tokens?|bearer tokens?",
}
# Frameworks in the detected stack that are a signal on their own.
SIGNAL_FRAMEWORKS: dict[str, set[str]] = {
    "web": {
        "fastapi",
        "flask",
        "django",
        "express",
        "nestjs",
        "koa",
        "hapi",
        "spring",
        "spring boot",
        "rails",
        "laravel",
        "aspnet",
        "asp.net",
        "gin",
        "echo",
        "fiber",
        "phoenix",
        "next",
        "nextjs",
        "nuxt",
        "sveltekit",
        "remix",
    },
    "browser": {
        "react",
        "vue",
        "angular",
        "svelte",
        "next",
        "nextjs",
        "nuxt",
        "sveltekit",
        "remix",
        "vite",
    },
    "api": {
        "fastapi",
        "flask",
        "django",
        "express",
        "nestjs",
        "spring",
        "spring boot",
        "gin",
        "echo",
        "fiber",
    },
    "graphql": {"graphql", "apollo", "graphene", "strawberry"},
    "websockets": {"socket.io", "channels"},
}
_PATTERNS = {
    name: re.compile(rf"(?<![\w-])(?:{words})(?![\w-])", re.IGNORECASE)
    for name, words in SIGNAL_WORDS.items()
}

# (rule id, signal, reason, ((test, mode, observed_by), ...)). The order is the table's.
RULES: tuple[tuple[str, str, str, tuple[tuple[str, str, str], ...]], ...] = (
    (
        "R-WEB",
        "web",
        "it serves HTTP: its answers' headers, methods, errors and exposed files",
        (
            ("CONF-04", AUTO, PROBE),
            ("CONF-06", AUTO, PROBE),
            ("CONF-07", AUTO, PROBE),
            ("CONF-12", AUTO, PROBE),
            ("CONF-14", AUTO, PROBE),
            ("CLNT-07", AUTO, PROBE),
            ("CLNT-09", AUTO, PROBE),
            ("ERRH-01", AUTO, PROBE),
            ("ERRH-02", AUTO, PROBE),
            ("INFO-02", MANUAL, PERSON),
            ("CONF-02", MANUAL, PERSON),
            ("CRYP-01", MANUAL, PERSON),
        ),
    ),
    (
        "R-BROWSER",
        "browser",
        "a browser renders what it serves: injected script and client-side storage",
        (
            ("INJT-01", AUTO, QA_TEST),
            ("INJT-02", AUTO, QA_TEST),
            ("CLNT-01", MANUAL, PERSON),
            ("CLNT-03", MANUAL, PERSON),
            ("CLNT-12", MANUAL, PERSON),
            ("CLNT-14", MANUAL, PERSON),
        ),
    ),
    (
        "R-AUTH",
        "auth",
        "people sign in: credentials, lockout, sessions and their cookies",
        (
            ("ATHN-01", MANUAL, PERSON),
            ("ATHN-02", MANUAL, PERSON),
            ("ATHN-03", AUTO, QA_TEST),
            ("ATHN-04", AUTO, QA_TEST),
            ("ATHN-06", MANUAL, PERSON),
            ("ATHN-07", MANUAL, PERSON),
            ("ATHN-09", MANUAL, PERSON),
            ("IDNT-04", AUTO, QA_TEST),
            ("SESS-01", MANUAL, PERSON),
            ("SESS-02", AUTO, PROBE),
            ("SESS-03", AUTO, QA_TEST),
            ("SESS-05", AUTO, QA_TEST),
            ("SESS-06", AUTO, QA_TEST),
            ("SESS-07", MANUAL, PERSON),
        ),
    ),
    (
        "R-ROLES",
        "roles",
        "what one person may do differs from another's: authorization on every route",
        (
            ("IDNT-01", MANUAL, PERSON),
            ("ATHZ-02", AUTO, QA_TEST),
            ("ATHZ-03", AUTO, QA_TEST),
            ("ATHZ-04", AUTO, QA_TEST),
        ),
    ),
    (
        "R-API",
        "api",
        "it exposes an API: its surface, the data it returns, verbs and mass assignment",
        (
            ("APIT-01", MANUAL, PERSON),
            ("APIT-03", MANUAL, PERSON),
            ("INJT-03", AUTO, QA_TEST),
            ("INJT-20", AUTO, QA_TEST),
        ),
    ),
    (
        "R-API-AUTH",
        "api+auth",
        "an API behind sign-in: object and function level authorization",
        (("APIT-02", AUTO, QA_TEST), ("APIT-04", AUTO, QA_TEST)),
    ),
    (
        "R-UPLOAD",
        "uploads",
        "it accepts files: their types, their content and where they land",
        (
            ("BUSL-08", AUTO, QA_TEST),
            ("BUSL-09", MANUAL, PERSON),
            ("ATHZ-01", AUTO, QA_TEST),
        ),
    ),
    (
        "R-DATABASE",
        "database",
        "it queries a database with input from outside",
        (("INJT-05", AUTO, QA_TEST),),
    ),
    (
        "R-OUTBOUND",
        "outbound",
        "it fetches URLs it is given",
        (("INJT-19", AUTO, QA_TEST),),
    ),
    (
        "R-PAYMENTS",
        "payments",
        "it takes payments: the amounts, the order of steps and their timing",
        (
            ("BUSL-10", MANUAL, PERSON),
            ("BUSL-01", MANUAL, PERSON),
            ("BUSL-04", MANUAL, PERSON),
        ),
    ),
    (
        "R-OAUTH",
        "oauth",
        "it delegates sign-in or access with OAuth or OpenID Connect",
        (("ATHZ-05", MANUAL, PERSON),),
    ),
    (
        "R-SAML",
        "saml",
        "it signs people in through SAML",
        (("ATHN-12", MANUAL, PERSON),),
    ),
    (
        "R-GRAPHQL",
        "graphql",
        "it exposes GraphQL: introspection, depth and batching",
        (("APIT-99", AUTO, QA_TEST),),
    ),
    (
        "R-WEBSOCKETS",
        "websockets",
        "it holds WebSocket connections: their origin and their messages",
        (("CLNT-10", AUTO, QA_TEST),),
    ),
    (
        "R-JWT",
        "jwt",
        "it issues or accepts JSON Web Tokens: their algorithm, signature and expiry",
        (("SESS-10", AUTO, QA_TEST),),
    ),
)


# --- the signals ---------------------------------------------------------------


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for k in sorted(value) for s in _strings(value[k])]
    if isinstance(value, list | tuple):
        return [s for v in value for s in _strings(v)]
    return []


def _places(snapshot: dict[str, Any]) -> list[tuple[str, str, str]]:
    """``(record, id, text)`` for every part of the records a signal may be read
    from, in a fixed order; *record* is ``specification`` or ``architecture``."""
    out: list[tuple[str, str, str]] = []
    spec = snapshot.get("specification") or {}
    if spec:
        head = " ".join(_strings([spec.get("title"), spec.get("summary")]))
        out.append(("specification", str(spec.get("id")), head))
        for field in ("requirements", "quality"):
            for item in spec.get(field) or []:
                label = item.get("id") if isinstance(item, dict) else None
                out.append(
                    ("specification", str(label or field), " ".join(_strings(item)))
                )
        constraints = " ".join(_strings(spec.get("constraints")))
        out.append(("specification", f"{spec.get('id')}", constraints))
    arch = snapshot.get("architecture") or {}
    if arch:
        head = " ".join(_strings([arch.get("title"), arch.get("overview")]))
        out.append(("architecture", str(arch.get("id")), head))
        for field in ("modules", "interfaces", "decisions", "conventions"):
            for item in arch.get(field) or []:
                label = item.get("id") if isinstance(item, dict) else None
                out.append(
                    ("architecture", str(label or field), " ".join(_strings(item)))
                )
    return out


def signals(snapshot: dict[str, Any]) -> dict[str, list[dict[str, str]]]:
    """Per signal found, where it was found (at most three places), as data:
    ``{"kind": "text", "record": "specification", "id": "REQ-2", "match": "log in"}``,
    ``{"kind": "interface", "id": "IF-1", "match": "http"}``, ``{"kind":
    "framework" | "database", "match": "fastapi"}`` or ``{"kind": "implied",
    "match": "auth"}``. Deterministic for the same records."""
    found: dict[str, list[dict[str, str]]] = {}

    def note(signal: str, where: dict[str, str]) -> None:
        places = found.setdefault(signal, [])
        if len(places) < _WHERE_LIMIT and where not in places:
            places.append(where)

    arch = snapshot.get("architecture") or {}
    for interface in arch.get("interfaces") or []:
        kind = (
            str((interface or {}).get("kind") or "").lower()
            if isinstance(interface, dict)
            else ""
        )
        if kind in ("http", "rest", "graphql", "websocket"):
            where = {"kind": "interface", "id": str(interface.get("id")), "match": kind}
            note("web", where)
            note(
                {"graphql": "graphql", "websocket": "websockets"}.get(kind, "api"),
                where,
            )
    stack = snapshot.get("stack") or {}
    frameworks = {str(f).lower() for f in stack.get("frameworks") or []}
    for signal, names in SIGNAL_FRAMEWORKS.items():
        for name in sorted(frameworks & names):
            note(signal, {"kind": "framework", "match": name})
    for name in stack.get("databases") or []:
        note("database", {"kind": "database", "match": str(name)})
    for record, ident, text in _places(snapshot):
        for signal, pattern in _PATTERNS.items():
            match = pattern.search(text)
            if match:
                note(
                    signal,
                    {
                        "kind": "text",
                        "record": record,
                        "id": ident,
                        "match": match.group(0).lower(),
                    },
                )
    # A sign-in, an API or a browser client implies an HTTP surface.
    for implied in (
        "auth",
        "api",
        "browser",
        "graphql",
        "websockets",
        "oauth",
        "saml",
        "jwt",
    ):
        if implied in found and "web" not in found:
            note("web", {"kind": "implied", "match": implied})
    return {name: found[name] for name in SIGNAL_WORDS if name in found}


# --- the plan --------------------------------------------------------------------


def fires(signal: str, found: dict[str, Any]) -> bool:
    """Whether a rule's signal is present; ``api+auth`` needs both."""
    return all(part in found for part in signal.split("+"))


def _selections() -> dict[str, list[tuple[str, str, str, str, str]]]:
    """Per test, the rules that can select it: ``(rule, signal, reason, mode, observed_by)``."""
    out: dict[str, list[tuple[str, str, str, str, str]]] = {}
    for rule, signal, reason, tests in RULES:
        for short, mode, by in tests:
            out.setdefault(short, []).append((rule, signal, reason, mode, by))
    return out


def plan(
    snapshot: dict[str, Any], proposals: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """The project's WSTG plan: every catalogue test as a row, applicable or not, with why."""
    found = signals(snapshot)
    selections = _selections()
    proposed = {}
    for p in proposals or []:
        short = wstg_catalogue.short_of(str(p.get("id") or ""))
        if short and wstg_catalogue.by_short(short) and short not in proposed:
            proposed[short] = p
    rows = []
    for test in wstg_catalogue.tests():
        short = test["short"]
        chosen = [s for s in selections.get(short, []) if fires(s[1], found)]
        row: dict[str, Any] = {
            "id": test["id"],
            "short": short,
            "name": test["name"],
            "category": test["category"],
            "applicable": bool(chosen),
            "rules": [s[0] for s in chosen],
            "signals": list(dict.fromkeys(s[1] for s in chosen)),
        }
        if chosen:
            # An automatic way wins over a manual one when two rules select it.
            mode, by = min(((s[3], s[4]) for s in chosen), key=lambda m: m[0] != AUTO)
            row.update(
                mode=mode,
                observed_by=by,
                reason="; ".join(f"{s[0]}: {s[2]}" for s in chosen),
            )
        elif short in proposed:
            p = proposed[short]
            row.update(
                applicable=True,
                mode=MANUAL,
                observed_by=PERSON,
                claim=True,
                proposed_by=str(p.get("role") or "a model")[:40],
                reason=f"proposed by {str(p.get('role') or 'a model')[:40]} (a claim, not a rule): {str(p.get('reason') or '')[:200]}",
            )
        else:
            would = list(dict.fromkeys(s[1] for s in selections.get(short, [])))
            row.update(
                mode=None,
                observed_by=None,
                reason=(
                    f"no signal of {', '.join(would)} in the specification, architecture or stack"
                    if would
                    else "no rule of the plan selects it"
                ),
                would_need=would,
            )
        rows.append(row)
    return {
        "catalogue": {"commit": wstg_catalogue.commit(), "licence": "CC BY-SA 4.0"},
        "signals": found,
        "rows": rows,
    }


def applicable(plan_: dict[str, Any]) -> list[dict[str, Any]]:
    return [r for r in plan_.get("rows") or [] if r.get("applicable")]


def test_token(short: str) -> str:
    """The test-name token of a WSTG test: ``ATHZ-02`` → ``wstg_athz_02``."""
    return "wstg_" + re.sub(r"[^a-z0-9]+", "_", short.lower()).strip("_")


def security_test_tasks(plan_: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    """QA's test-writing tasks, one per applicable ``auto`` / ``qa_test`` row, at most *limit*.

    Shaped like the acceptance test tasks (``services/evidence/acceptance.py``)
    so a sprint plan can carry them; the test's name carries the row's token,
    which ``services/evidence/wstg_tests.py`` reads back from the daemon's run.
    """
    tasks = []
    for row in applicable(plan_):
        if row.get("observed_by") != QA_TEST or len(tasks) >= max(0, limit):
            continue
        token = test_token(row["short"])
        tasks.append(
            {
                "title": f"Security test {row['id']}: {row['name']}"[:80],
                "description": (
                    f"Write one automated test for OWASP WSTG {row['id']} ({row['name']}) "
                    f"against this project, named test_{token}_<what it checks>, in the "
                    "project's test framework. It passes when the application resists "
                    "the test and fails when it does not; it never weakens or skips the "
                    f"check. Why it applies: {row['reason']}"
                )[:DESCRIPTION_MAX],
                "priority": "medium",
                "assignee": QA_ROLE,
                "covers": [],
                "depends_on": [],
                "kind": None,
                "wstg_id": row["id"],
            }
        )
    return tasks
