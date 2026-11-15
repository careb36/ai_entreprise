"""The Verifier's ``security_probe`` check: the application's answers as WSTG items.

With the application check (``DELIVERY_APP_CHECK_ENABLED``) the loop asks the
daemon to probe the running application too (``security_probe: true`` on
``workspace.app.check``, ``vscode-daemon/security_probe.py``, issue #540). The
reply is kept as ``evidence["app_run"]["security_probe"]``; this module reads
it and nothing else: never the producer's claims, never a value the producer
reported (rules 1, 2 and 6 of ``docs/POSITIONING.md``).

Each item is one OWASP Web Security Testing Guide test, named by its versioned
identifier (``WSTG-<version>-<category>-<number>``, the guide's own format).
The tests that exist in release 4.2 carry ``v42``; Content Security Policy
(CONF-12) and Other HTTP Security Header Misconfigurations (CONF-14) are not in
4.2, so they carry the commit of the guide they were read from. Only the IDs
and names are used here, never the guide's text (CC BY-SA 4.0).

An item's status is one of:

- ``refuted``: the answer shows a weakness the delivery must not ship: a
  credentialed CORS answer to any origin (``*``, the foreign origin echoed or
  ``null`` with ``Access-Control-Allow-Credentials: true``), a stack trace in an
  error body, a served ``/.git/HEAD`` or ``/.env``;
- ``finding``: a missing header or cookie attribute, TRACE enabled, a server
  error on a bad request, a version in ``Server``: for the reviewer;
- ``unverifiable``: what plain http cannot show (HSTS, and a cookie without
  ``Secure``, which an application may add only over https), an answer that
  did not come, or nothing to observe (no cookie set): never a pass;
- ``pass``: the answer was observed and is as it should be.

The check's outcome is ``refuted`` when any item is, ``unverifiable`` when no
probe was observed (an older daemon, no ``python3`` in the image) and ``pass``
otherwise: the items keep their own statuses, the findings and the
unverifiable ones named in the observation.
"""

from __future__ import annotations

import re
from typing import Any

from services.contracts.verdicts import VERDICT_REFUTED, VERDICT_UNVERIFIABLE

CHECK_NAME = "security_probe"
EVIL_ORIGIN = "https://evil.example"
WSTG_COMMIT = "87cf4a4688ba49a64068adf6246496cf5459f709"
_AT_COMMIT = WSTG_COMMIT[:7]

PASS, FINDING, REFUTED, UNVERIFIABLE = "pass", "finding", "refuted", "unverifiable"
_RANK = {PASS: 0, UNVERIFIABLE: 1, FINDING: 2, REFUTED: 3}

# (identifier, name) per test; the names as the guide's release (or commit) titles them.
WSTG = {
    "CONF-04": (
        "WSTG-v42-CONF-04",
        "Review Old Backup and Unreferenced Files for Sensitive Information",
    ),
    "CONF-06": ("WSTG-v42-CONF-06", "Test HTTP Methods"),
    "CONF-07": ("WSTG-v42-CONF-07", "Test HTTP Strict Transport Security"),
    "CONF-12": (f"WSTG-{_AT_COMMIT}-CONF-12", "Content Security Policy"),
    "CONF-14": (
        f"WSTG-{_AT_COMMIT}-CONF-14",
        "Other HTTP Security Header Misconfigurations",
    ),
    "SESS-02": ("WSTG-v42-SESS-02", "Testing for Cookies Attributes"),
    "CLNT-07": ("WSTG-v42-CLNT-07", "Testing Cross Origin Resource Sharing"),
    "CLNT-09": ("WSTG-v42-CLNT-09", "Testing for Clickjacking"),
    "ERRH-01": ("WSTG-v42-ERRH-01", "Testing for Improper Error Handling"),
    "ERRH-02": ("WSTG-v42-ERRH-02", "Testing for Stack Traces"),
}

# What a stack trace looks like in an error body, per runtime.
_TRACE_PATTERNS = (
    ("python", re.compile(r"Traceback \(most recent call last\)")),
    ("python", re.compile(r'File "[^"]+", line \d+, in ')),
    ("java", re.compile(r"\bat [\w$.]+\([\w$]+\.java:\d+\)")),
    ("java", re.compile(r"Exception in thread \"")),
    ("node", re.compile(r"\n\s+at [^\n]*\(?/[^\n]*:\d+:\d+\)?")),
    ("php", re.compile(r"(?:Fatal error|Warning|Parse error): .* on line \d+")),
    ("php", re.compile(r"Stack trace:\s*\n?\s*#0 ")),
    ("ruby", re.compile(r"\.rb:\d+:in `")),
    ("dotnet", re.compile(r"\bat [\w.]+\([^)]*\) in [^\n]+:line \d+")),
    ("go", re.compile(r"goroutine \d+ \[running\]:")),
    ("werkzeug", re.compile(r"Werkzeug Debugger")),
)
_GIT_HEAD = re.compile(r"^(?:ref: refs/|[0-9a-f]{40}\b)")
_VERSION = re.compile(r"\d+\.\d+")


def _headers(entry: dict[str, Any]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for pair in entry.get("headers") or []:
        if isinstance(pair, list) and len(pair) == 2:
            out.setdefault(str(pair[0]).lower(), []).append(str(pair[1]))
    return out


def _first(headers: dict[str, list[str]], name: str) -> str | None:
    values = headers.get(name)
    return values[0].strip() if values else None


def _status(entry: dict[str, Any]) -> int | None:
    status = entry.get("status")
    return status if isinstance(status, int) and not isinstance(status, bool) else None


def _cited(entry: dict[str, Any]) -> str:
    """The request as sent, cited in the observation (the command, as a log would)."""
    request = entry.get("request") or {}
    return f"{request.get('method') or '?'} {request.get('path') or '?'}"


def _no_answer(entry: dict[str, Any]) -> str:
    return f"{_cited(entry)} got no answer ({entry.get('error') or 'not sent'})"


def _item(key: str, status: str, observation: str, **extra: Any) -> dict[str, Any]:
    wstg_id, name = WSTG[key]
    return {
        "id": wstg_id,
        "name": name,
        "status": status,
        "observation": observation,
        **extra,
    }


def _worst(statuses: list[str]) -> str:
    return max(statuses, key=_RANK.__getitem__) if statuses else PASS


# ---------------------------------------------------------------------------
# One function per WSTG test
# ---------------------------------------------------------------------------


def _hsts(probe: dict[str, Any], get: dict[str, Any]) -> dict[str, Any]:
    if probe.get("scheme") != "https":
        return _item(
            "CONF-07",
            UNVERIFIABLE,
            f"served over plain {probe.get('scheme') or 'http'} at "
            f"{probe.get('base_url')}: Strict-Transport-Security applies only over "
            "https, so this run cannot observe it",
        )
    if _status(get) is None:
        return _item("CONF-07", UNVERIFIABLE, _no_answer(get))
    value = _first(_headers(get), "strict-transport-security")
    match = re.search(r"max-age\s*=\s*(\d+)", value or "", re.IGNORECASE)
    if match and int(match.group(1)) > 0:
        return _item("CONF-07", PASS, f"Strict-Transport-Security: {value}")
    return _item(
        "CONF-07",
        FINDING,
        f"{_cited(get)} has no Strict-Transport-Security with a max-age"
        + (f" ({value})" if value else ""),
    )


def _csp(get: dict[str, Any]) -> dict[str, Any]:
    if _status(get) is None:
        return _item("CONF-12", UNVERIFIABLE, _no_answer(get))
    value = _first(_headers(get), "content-security-policy")
    if not value:
        return _item(
            "CONF-12", FINDING, f"{_cited(get)} has no Content-Security-Policy"
        )
    weak = [w for w in ("'unsafe-inline'", "'unsafe-eval'") if w in value.lower()]
    if weak:
        return _item(
            "CONF-12",
            FINDING,
            f"Content-Security-Policy allows {', '.join(weak)}: {value[:200]}",
        )
    return _item("CONF-12", PASS, f"Content-Security-Policy: {value[:200]}")


def _other_headers(get: dict[str, Any]) -> dict[str, Any]:
    if _status(get) is None:
        return _item("CONF-14", UNVERIFIABLE, _no_answer(get))
    headers = _headers(get)
    problems = []
    if (_first(headers, "x-content-type-options") or "").lower() != "nosniff":
        problems.append("no X-Content-Type-Options: nosniff")
    if not _first(headers, "referrer-policy"):
        problems.append("no Referrer-Policy")
    for name in ("server", "x-powered-by"):
        value = _first(headers, name)
        if value and _VERSION.search(value):
            problems.append(f"{name.title()} names a version ({value[:80]})")
    if problems:
        return _item("CONF-14", FINDING, f"{_cited(get)}: " + "; ".join(problems))
    return _item(
        "CONF-14",
        PASS,
        f"{_cited(get)} sends X-Content-Type-Options and Referrer-Policy "
        "and names no server version",
    )


def _clickjacking(get: dict[str, Any]) -> dict[str, Any]:
    if _status(get) is None:
        return _item("CLNT-09", UNVERIFIABLE, _no_answer(get))
    headers = _headers(get)
    frame = (_first(headers, "x-frame-options") or "").upper()
    csp = (_first(headers, "content-security-policy") or "").lower()
    if frame in ("DENY", "SAMEORIGIN") or "frame-ancestors" in csp:
        how = f"X-Frame-Options: {frame}" if frame else "CSP frame-ancestors"
        return _item("CLNT-09", PASS, f"{_cited(get)} limits framing ({how})")
    return _item(
        "CLNT-09",
        FINDING,
        f"{_cited(get)} has neither X-Frame-Options nor a CSP frame-ancestors",
    )


def _cookie(probe: dict[str, Any], cookie: dict[str, Any]) -> tuple[str, list[str]]:
    name = str(cookie.get("name") or "?")
    notes: list[str] = []
    statuses: list[str] = []
    if not cookie.get("httponly"):
        statuses.append(FINDING)
        notes.append(f"{name}: no HttpOnly")
    samesite = str(cookie.get("samesite") or "")
    if not samesite:
        statuses.append(FINDING)
        notes.append(f"{name}: no SameSite")
    if not cookie.get("secure"):
        if samesite.lower() == "none":
            statuses.append(FINDING)
            notes.append(f"{name}: SameSite=None without Secure")
        elif probe.get("scheme") != "https":
            statuses.append(UNVERIFIABLE)
            notes.append(
                f"{name}: no Secure over plain http (an application may add it "
                "only over https; this run cannot tell)"
            )
        else:
            statuses.append(FINDING)
            notes.append(f"{name}: no Secure over https")
    return _worst(statuses), notes


def _cookies(probe: dict[str, Any], get: dict[str, Any]) -> dict[str, Any]:
    if _status(get) is None:
        return _item("SESS-02", UNVERIFIABLE, _no_answer(get))
    cookies = [c for c in get.get("cookies") or [] if isinstance(c, dict)]
    if not cookies:
        return _item(
            "SESS-02",
            UNVERIFIABLE,
            f"{_cited(get)} set no cookie: no cookie attributes to observe "
            "(cookies set by other paths were not probed)",
        )
    results = [_cookie(probe, cookie) for cookie in cookies]
    status = _worst([s for s, _ in results])
    notes = [n for _, ns in results for n in ns]
    names = ", ".join(str(c.get("name") or "?") for c in cookies)
    if not notes:
        return _item(
            "SESS-02",
            PASS,
            f"{_cited(get)} set {names}: HttpOnly, SameSite and Secure on each",
            cookies=cookies,
        )
    return _item(
        "SESS-02", status, f"{_cited(get)}: " + "; ".join(notes), cookies=cookies
    )


def _cors_answer(entry: dict[str, Any]) -> tuple[str | None, bool]:
    headers = _headers(entry)
    origin = _first(headers, "access-control-allow-origin")
    credentials = (
        _first(headers, "access-control-allow-credentials") or ""
    ).lower() == "true"
    return origin, credentials


def _cors(requests: dict[str, Any]) -> dict[str, Any]:
    entries = [
        requests.get(k) or {} for k in ("cors_origin", "cors_credentials", "preflight")
    ]
    answered = [e for e in entries if _status(e) is not None]
    if not answered:
        return _item("CLNT-07", UNVERIFIABLE, _no_answer(entries[0]))
    refuted, findings = [], []
    for entry in answered:
        origin, credentials = _cors_answer(entry)
        said = (
            f"{_cited(entry)} from Origin: {EVIL_ORIGIN} answered "
            f"Access-Control-Allow-Origin: {origin}"
            + (", Access-Control-Allow-Credentials: true" if credentials else "")
        )
        if origin in ("*", EVIL_ORIGIN, "null") and credentials:
            refuted.append(said)
        elif origin in (EVIL_ORIGIN, "null"):
            findings.append(said + " (any origin is reflected)")
        elif origin == "*":
            findings.append(said + " (any origin may read it)")
    if refuted:
        return _item("CLNT-07", REFUTED, "; ".join(refuted))
    if findings:
        return _item("CLNT-07", FINDING, "; ".join(findings))
    return _item(
        "CLNT-07",
        PASS,
        f"no Access-Control-Allow-Origin granted to {EVIL_ORIGIN} "
        f"({len(answered)} request(s) answered)",
    )


def _methods(requests: dict[str, Any]) -> dict[str, Any]:
    trace = requests.get("trace") or {}
    options = requests.get("options") or {}
    if _status(trace) is None and _status(options) is None:
        return _item("CONF-06", UNVERIFIABLE, _no_answer(trace))
    allow = _first(_headers(options), "allow") or ""
    notes, status = [], PASS
    trace_status = _status(trace)
    if trace_status is not None and 200 <= trace_status < 300:
        status = FINDING
        echoed = " and echoed the probe's header" if trace.get("echoed") else ""
        notes.append(f"{_cited(trace)} answered {trace_status}{echoed}")
    elif trace_status is not None:
        notes.append(f"{_cited(trace)} answered {trace_status}")
    if "TRACE" in allow.upper():
        status = FINDING
        notes.append("Allow names TRACE")
    if _status(options) is not None:
        notes.append(
            f"{_cited(options)} answered {_status(options)}"
            + (f" (Allow: {allow[:120]})" if allow else "")
        )
    return _item("CONF-06", status, "; ".join(notes))


def _traces_in(body: str) -> list[str]:
    return sorted(
        {runtime for runtime, pattern in _TRACE_PATTERNS if pattern.search(body)}
    )


def _errors(requests: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    entries = [requests.get(k) or {} for k in ("not_found", "malformed")]
    answered = [e for e in entries if _status(e) is not None]
    if not answered:
        no = _no_answer(entries[0])
        return _item("ERRH-01", UNVERIFIABLE, no), _item("ERRH-02", UNVERIFIABLE, no)
    handling, handled = [], []
    traces = []
    for entry in answered:
        status = _status(entry)
        if status is not None and status >= 500:
            handling.append(f"{_cited(entry)} answered {status}")
        else:
            handled.append(f"{_cited(entry)} answered {status}")
        found = _traces_in(str(entry.get("body_head") or ""))
        if found:
            traces.append(
                f"{_cited(entry)}: a {', '.join(found)} stack trace in the body"
            )
    errh01 = (
        _item("ERRH-01", FINDING, "; ".join(handling + handled))
        if handling
        else _item("ERRH-01", PASS, "; ".join(handled))
    )
    errh02 = (
        _item("ERRH-02", REFUTED, "; ".join(traces))
        if traces
        else _item(
            "ERRH-02",
            PASS,
            f"no stack trace in the error bodies of {len(answered)} request(s)",
        )
    )
    return errh01, errh02


def _served(requests: dict[str, Any]) -> dict[str, Any]:
    git = requests.get("git_head") or {}
    env = requests.get("env_file") or {}
    if _status(git) is None and _status(env) is None:
        return _item("CONF-04", UNVERIFIABLE, _no_answer(git))
    refuted, notes = [], []
    if _status(git) == 200 and _GIT_HEAD.match(str(git.get("body_head") or "")):
        refuted.append(f"{_cited(git)} served the repository's HEAD")
    elif _status(git) is not None:
        notes.append(f"{_cited(git)} answered {_status(git)}, not a git HEAD")
    content_type = (_first(_headers(env), "content-type") or "").lower()
    keys = [str(k) for k in env.get("env_keys") or []]
    if _status(env) == 200 and keys and "html" not in content_type:
        refuted.append(
            f"{_cited(env)} served an environment file assigning {', '.join(keys[:5])}"
        )
    elif _status(env) is not None:
        notes.append(f"{_cited(env)} answered {_status(env)}, not an environment file")
    if refuted:
        return _item("CONF-04", REFUTED, "; ".join(refuted))
    return _item("CONF-04", PASS, "; ".join(notes))


# ---------------------------------------------------------------------------
# The Verifier's check
# ---------------------------------------------------------------------------


def items(probe: dict[str, Any]) -> list[dict[str, Any]]:
    """The WSTG items the daemon's observation supports, in a fixed order."""
    requests = probe.get("requests") or {}
    get = requests.get("get") or {}
    errh01, errh02 = _errors(requests)
    return [
        _cors(requests),
        _served(requests),
        errh02,
        errh01,
        _methods(requests),
        _hsts(probe, get),
        _csp(get),
        _other_headers(get),
        _clickjacking(get),
        _cookies(probe, get),
    ]


def check(evidence: dict[str, Any] | None) -> dict[str, Any] | None:
    """``security_probe``: absent unless the application listened and the probe
    was asked for (``security_probe_requested``) or answered."""
    app = (evidence or {}).get("app_run")
    if not isinstance(app, dict) or not app.get("listening"):
        return None
    probe = app.get("security_probe")
    if probe is None and not app.get("security_probe_requested"):
        return None
    base = {"name": CHECK_NAME, "observed_by": "daemon", "wstg_commit": WSTG_COMMIT}
    if not isinstance(probe, dict):
        return {
            **base,
            "outcome": VERDICT_UNVERIFIABLE,
            "observation": "security probe not observed: the daemon did not answer "
            "it (a daemon from before the probe)",
            "items": [],
        }
    if not probe.get("observed"):
        return {
            **base,
            "outcome": VERDICT_UNVERIFIABLE,
            "observation": "security probe not observed: "
            + str(probe.get("reason") or "no reason given"),
            "items": [],
        }
    found = items(probe)
    by_status: dict[str, list[str]] = {}
    for item in found:
        by_status.setdefault(item["status"], []).append(item["id"])
    where = f" ({probe.get('ran_in')})" if probe.get("ran_in") else ""
    parts = [
        f"security probe of {probe.get('base_url')}{probe.get('path') or '/'}{where}: "
        + ", ".join(
            f"{len(by_status.get(s, []))} {s}"
            for s in (REFUTED, FINDING, UNVERIFIABLE, PASS)
        )
    ]
    for item in found:
        if item["status"] in (REFUTED, FINDING):
            parts.append(f"{item['id']} {item['status']}: {item['observation']}")
    unverifiable = by_status.get(UNVERIFIABLE)
    if unverifiable:
        parts.append(f"unverifiable: {', '.join(unverifiable)}")
    return {
        **base,
        "outcome": VERDICT_REFUTED if REFUTED in by_status else "pass",
        "observation": "; ".join(parts),
        "items": found,
    }
