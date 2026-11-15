"""The application's answers to a few security probes (issue #540).

With ``security_probe: true`` on ``workspace.app.check``, once the
application listens, the daemon sends a fixed set of requests to it inside
the same run and sandbox and records what came back, bounded:

- ``get``: the probe path's response headers, all of them (each value cut at
  ``VALUE_CHARS``), and every ``Set-Cookie`` parsed into its attributes; a
  cookie's value is never kept, only its length;
- ``cors_origin`` / ``cors_credentials``: the same GET with
  ``Origin: https://evil.example``, without and with a cookie, and
  ``preflight``: an OPTIONS preflight from that origin; the
  ``Access-Control-*`` and ``Vary`` headers of each;
- ``options`` and ``trace``: the answers to OPTIONS and TRACE (whether TRACE
  echoed a header the probe sent, never the body);
- ``not_found`` and ``malformed``: a path that does not exist and a POST with
  a truncated JSON body, with the head of each body (``BODY_HEAD_CHARS``);
- ``git_head`` and ``env_file``: ``/.git/HEAD`` (the head of its body) and
  ``/.env`` (its size and the names its lines assign, never a value).

The daemon records; the backend judges (``services/evidence/security_probe.py``
maps each item to its OWASP WSTG test). Nothing here comes from the producer.

The module is standard library only and runs two ways: imported by the daemon
in host mode (``observe``), or as a script inside the application check's
container (``python3 -c <this file> <base_url> <path>``), which prints one
line the daemon reads back (``read``) and bounds again.
"""

from __future__ import annotations

import http.client
import json
import re
import secrets
import sys
import time
import urllib.parse
from typing import Any

MARK = "fabrique-security-probe: "
EVIL_ORIGIN = "https://evil.example"
REQUEST_SECONDS = 5
TOTAL_SECONDS = 40
# The daemon's bound on the in-container run: the probe's own bound and a margin.
PROBE_SECONDS = TOTAL_SECONDS + 20
MAX_BODY_BYTES = 65536
BODY_HEAD_CHARS = 2000
GIT_HEAD_CHARS = 200
MAX_HEADERS = 40
VALUE_CHARS = 1024
MAX_COOKIES = 20
MAX_ENV_KEYS = 20
OUTPUT_CHARS = 1000

_CORS = ("access-control-allow-origin", "access-control-allow-credentials",
         "access-control-allow-methods", "access-control-allow-headers", "vary")
_ERROR = ("content-type", "content-length", "server", "x-powered-by")
# Which response headers each probe keeps; ``None`` keeps them all.
_KEEP: dict[str, tuple[str, ...] | None] = {
    "get": None,
    "cors_origin": _CORS,
    "cors_credentials": _CORS,
    "preflight": _CORS + ("allow",),
    "options": ("allow",) + _CORS,
    "trace": ("content-type",),
    "not_found": _ERROR,
    "malformed": _ERROR,
    "git_head": ("content-type", "content-length"),
    "env_file": ("content-type", "content-length"),
}
_ENV_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=", re.MULTILINE)


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit]


def parse_cookie(header: str) -> dict[str, Any]:
    """One ``Set-Cookie`` value as its attributes; the value only as a length."""
    first, _, rest = header.partition(";")
    name, _, value = first.partition("=")
    cookie: dict[str, Any] = {
        "name": _cut(name.strip(), 128),
        "value_bytes": len(value.strip().encode("utf-8")),
        "secure": False,
        "httponly": False,
        "samesite": None,
        "attributes": _cut(f";{rest}" if rest else "", VALUE_CHARS),
    }
    for part in rest.split(";"):
        key, _, val = part.strip().partition("=")
        key = key.strip().lower()
        if key == "secure":
            cookie["secure"] = True
        elif key == "httponly":
            cookie["httponly"] = True
        elif key == "samesite":
            cookie["samesite"] = _cut(val.strip(), 32)
        elif key in ("path", "domain", "expires", "max-age"):
            cookie[key.replace("-", "_")] = _cut(val.strip(), 128)
    return cookie


def _redacted_cookie(header: str) -> str:
    """A ``Set-Cookie`` header as kept: the value replaced by its length."""
    cookie = parse_cookie(header)
    return _cut(f"{cookie['name']}=<{cookie['value_bytes']} bytes>{cookie['attributes']}", VALUE_CHARS)


def _headers(pairs: list[tuple[str, str]], keep: tuple[str, ...] | None) -> list[list[str]]:
    out = []
    for name, value in pairs:
        lower = name.lower()
        if keep is not None and lower not in keep:
            continue
        if lower == "set-cookie":
            value = _redacted_cookie(value)
        out.append([_cut(name, 128), _cut(value, VALUE_CHARS)])
        if len(out) >= MAX_HEADERS:
            break
    return out


def _send(base: urllib.parse.SplitResult, key: str, method: str, path: str,
          headers: dict[str, str], body: bytes | None = None, echo: str = "") -> dict[str, Any]:
    entry: dict[str, Any] = {"request": {"method": method, "path": path, "headers": dict(headers)}}
    conn_type = http.client.HTTPSConnection if base.scheme == "https" else http.client.HTTPConnection
    conn = conn_type(base.hostname or "127.0.0.1", base.port, timeout=REQUEST_SECONDS)
    began = time.monotonic()
    try:
        conn.request(method, path, body=body, headers=headers)
        response = conn.getresponse()
        data = response.read(MAX_BODY_BYTES)
        pairs = response.getheaders()
    except (OSError, http.client.HTTPException, ValueError) as exc:
        entry.update({"status": None, "error": _cut(f"{type(exc).__name__}: {exc}", 300)})
        return entry
    finally:
        conn.close()
    entry.update({
        "status": response.status,
        "elapsed_ms": int((time.monotonic() - began) * 1000),
        "headers": _headers(pairs, _KEEP[key]),
        "body_bytes": len(data),
    })
    if key == "get":
        cookies = [parse_cookie(v) for n, v in pairs if n.lower() == "set-cookie"]
        entry["cookies"] = cookies[:MAX_COOKIES]
    text = data.decode("utf-8", errors="replace")
    if key in ("not_found", "malformed"):
        entry["body_head"] = text[:BODY_HEAD_CHARS]
    elif key == "git_head":
        entry["body_head"] = text[:GIT_HEAD_CHARS]
    elif key == "env_file":
        entry["env_keys"] = list(dict.fromkeys(_ENV_LINE.findall(text)))[:MAX_ENV_KEYS]
    elif key == "trace":
        # Whether the answer reflected the probe's own header; the body is not kept.
        entry["echoed"] = 200 <= response.status < 300 and bool(echo) and echo in text
    return entry


def observe(base_url: str, path: str = "/") -> dict[str, Any]:
    """Send the probes to the application at *base_url* and record the answers."""
    base = urllib.parse.urlsplit(base_url)
    path = path if path.startswith("/") else f"/{path}"
    token = secrets.token_hex(8)
    missing = f"/fabrique-probe-{token}-missing"
    accept = {"Accept": "text/html,application/json;q=0.9,*/*;q=0.8"}
    plan = (
        ("get", "GET", path, accept, None),
        ("cors_origin", "GET", path, {**accept, "Origin": EVIL_ORIGIN}, None),
        ("cors_credentials", "GET", path,
         {**accept, "Origin": EVIL_ORIGIN, "Cookie": "fabrique_probe=1"}, None),
        ("preflight", "OPTIONS", path,
         {"Origin": EVIL_ORIGIN, "Access-Control-Request-Method": "PUT",
          "Access-Control-Request-Headers": "authorization"}, None),
        ("options", "OPTIONS", path, {}, None),
        ("trace", "TRACE", path, {"X-Fabrique-Probe": token}, None),
        ("not_found", "GET", missing, accept, None),
        ("malformed", "POST", path,
         {"Content-Type": "application/json", "Accept": "application/json"}, b'{"fabrique": '),
        ("git_head", "GET", "/.git/HEAD", {}, None),
        ("env_file", "GET", "/.env", {}, None),
    )
    requests: dict[str, Any] = {}
    began = time.monotonic()
    for key, method, target, headers, body in plan:
        if time.monotonic() - began > TOTAL_SECONDS:
            requests[key] = {"request": {"method": method, "path": target, "headers": headers},
                             "status": None, "error": f"not sent: the probe's {TOTAL_SECONDS}s bound was reached"}
            continue
        requests[key] = _send(base, key, method, target, headers, body, echo=token)
    return {
        "observed": True,
        "observed_by": "daemon",
        "base_url": base_url,
        "scheme": base.scheme,
        "path": path,
        "seconds": round(time.monotonic() - began, 2),
        "requests": requests,
    }


def unavailable(reason: str, **extra: Any) -> dict[str, Any]:
    return {"observed": False, "observed_by": "daemon", "reason": reason, **extra}


# ---------------------------------------------------------------------------
# The daemon's side: run it where the application runs, read it back
# ---------------------------------------------------------------------------


def _bounded_entry(key: str, entry: Any) -> dict[str, Any]:
    """An entry the in-container run printed, cut to the same bounds as here."""
    if not isinstance(entry, dict):
        return {"status": None, "error": "the probe printed no entry"}
    out: dict[str, Any] = {}
    request = entry.get("request")
    if isinstance(request, dict):
        out["request"] = {k: request.get(k) for k in ("method", "path", "headers")}
    status = entry.get("status")
    out["status"] = status if isinstance(status, int) and not isinstance(status, bool) else None
    if isinstance(entry.get("error"), str):
        out["error"] = _cut(entry["error"], 300)
    headers = entry.get("headers")
    if isinstance(headers, list):
        pairs = [(str(h[0]), str(h[1])) for h in headers if isinstance(h, list) and len(h) == 2]
        out["headers"] = [[_cut(n, 128), _cut(v, VALUE_CHARS)] for n, v in pairs
                          if _KEEP[key] is None or n.lower() in _KEEP[key]][:MAX_HEADERS]
    if isinstance(entry.get("body_head"), str):
        out["body_head"] = entry["body_head"][:GIT_HEAD_CHARS if key == "git_head" else BODY_HEAD_CHARS]
    for name in ("body_bytes", "elapsed_ms"):
        if isinstance(entry.get(name), int):
            out[name] = entry[name]
    if key == "get" and isinstance(entry.get("cookies"), list):
        keys = ("name", "value_bytes", "secure", "httponly", "samesite", "attributes",
                "path", "domain", "expires", "max_age")
        out["cookies"] = [{k: c[k] for k in keys if k in c} for c in entry["cookies"]
                          if isinstance(c, dict)][:MAX_COOKIES]
    if key == "env_file" and isinstance(entry.get("env_keys"), list):
        out["env_keys"] = [_cut(str(k), 128) for k in entry["env_keys"]][:MAX_ENV_KEYS]
    if key == "trace":
        out["echoed"] = entry.get("echoed") is True
    return out


def read(run: dict[str, Any]) -> dict[str, Any]:
    """The observation from the in-container run (``sandbox.run_bounded``'s result)."""
    text = f"{run.get('stdout') or ''}"
    for line in reversed(text.splitlines()):
        if line.startswith(MARK):
            try:
                said = json.loads(line[len(MARK):])
            except ValueError:
                break
            if isinstance(said, dict) and isinstance(said.get("requests"), dict):
                requests = {key: _bounded_entry(key, said["requests"].get(key)) for key in _KEEP}
                return {
                    "observed": True, "observed_by": "daemon", "ran_in": "container",
                    "base_url": str(said.get("base_url") or ""), "scheme": str(said.get("scheme") or ""),
                    "path": str(said.get("path") or "/"), "requests": requests,
                }
            break
    output = f"{text}{run.get('stderr') or ''}".strip()[-OUTPUT_CHARS:]
    code = run.get("exit_code")
    if run.get("timed_out"):
        return unavailable(f"the probe did not finish within {PROBE_SECONDS}s", output_tail=output)
    if code == 127 or "python3: not found" in output or "executable file not found" in output:
        return unavailable("no python3 in the sandbox image to send the security probe with",
                           exit_code=code, output_tail=output)
    return unavailable(f"the probe gave no answer (exit {code})", exit_code=code, output_tail=output)


def on_host(base_url: str, path: str) -> dict[str, Any]:
    """The probe from the daemon's own process, against the application on this machine."""
    try:
        return {**observe(base_url, path), "ran_in": "host"}
    except Exception as exc:  # noqa: BLE001 - Whatever went wrong is the observation.
        return unavailable(f"the probe failed: {type(exc).__name__}: {exc}")


def script() -> str | None:
    try:
        with open(__file__, encoding="utf-8") as handle:
            return handle.read()
    except OSError:
        return None


def in_container(runtime: str, name: str, base_url: str, path: str, run_bounded) -> dict[str, Any]:
    """The probe inside the running application check's container."""
    source = script()
    if source is None:
        return unavailable("the probe's source is missing from this daemon")
    return read(run_bounded(
        [runtime, "exec", name, "python3", "-c", source, base_url, path], PROBE_SECONDS,
    ))


def main(argv: list[str]) -> int:
    base_url = argv[0] if argv else "http://127.0.0.1:8000"
    path = argv[1] if len(argv) > 1 else "/"
    print(MARK + json.dumps(observe(base_url, path)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
