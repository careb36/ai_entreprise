"""The security probe in the application check (issue #540).

The daemon (``vscode-daemon/security_probe.py``, through ``app_run``) sends
the running application a fixed set of requests and records the answers;
the evidence (``services/evidence/security_probe.py``) maps them to OWASP
WSTG items: refuted, finding, unverifiable or pass. Real servers in
``tmp_path`` with good and bad header, cookie and CORS configurations; the
producer's own report is never read as the probe.
"""

from __future__ import annotations

import asyncio
import json
import socket
import sys
import textwrap
from pathlib import Path

import app_run  # the daemon's module, on sys.path via conftest
import pytest
import sandbox
import security_probe as daemon_probe
from config import settings
from jsonschema import Draft202012Validator
from models.agent_run import AgentRun
from models.company import Company
from services.evidence import delivery_gate, security_probe
from services.evidence.verification import verify_agent_run
from services.loop import delivery_checks
from services.workspace import project_profile, stack_profiles

PY = sys.executable
SCHEMA = (
    Path(__file__).resolve().parents[2]
    / "vscode-daemon/protocol/schemas/v1/workspace.app.check.json"
)
COOKIE_SECRET = "s3ss10n-value-never-kept"
ENV_SECRET = "value-of-the-env-file-never-kept"

# One server, configured per test: a dict of what it does, read from a file.
SERVER = textwrap.dedent(
    """
    import http.server, json, os, sys
    conf = json.load(open("conf.json"))
    class H(http.server.BaseHTTPRequestHandler):
        server_version = conf.get("server", "app")
        def version_string(self):
            return self.server_version
        def log_message(self, *a):
            pass
        def _cors(self):
            origin = self.headers.get("Origin")
            if origin and conf.get("cors") == "reflect":
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Access-Control-Allow-Credentials", "true")
            elif origin and conf.get("cors") == "wildcard-credentials":
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Credentials", "true")
            elif origin and conf.get("cors") == "own":
                self.send_header("Access-Control-Allow-Origin", "https://app.example")
        def _send(self, status, body=b"", headers=()):
            self.send_response(status)
            for name, value in headers:
                self.send_header(name, value)
            self._cors()
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def do_GET(self):
            if self.path == "/":
                headers = list(conf.get("headers", {}).items())
                headers += [("Set-Cookie", c) for c in conf.get("cookies", [])]
                return self._send(200, b"<h1>hi</h1>", headers + [("Content-Type", "text/html")])
            if self.path == "/.git/HEAD" and conf.get("serve_git"):
                return self._send(200, b"ref: refs/heads/main\\n", [("Content-Type", "text/plain")])
            if self.path == "/.env" and conf.get("serve_env"):
                body = ("API_TOKEN=" + conf["env_secret"] + "\\nDEBUG=1\\n").encode()
                return self._send(200, body, [("Content-Type", "text/plain")])
            return self._send(404, b"not found", [("Content-Type", "text/plain")])
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            if conf.get("traceback"):
                body = (
                    "Traceback (most recent call last):\\n"
                    '  File "/srv/app.py", line 12, in handle\\n'
                    "json.decoder.JSONDecodeError: Expecting value\\n"
                ).encode()
                return self._send(500, body, [("Content-Type", "text/plain")])
            return self._send(400, b"bad request", [("Content-Type", "text/plain")])
        def do_OPTIONS(self):
            return self._send(204, b"", [("Allow", conf.get("allow", "GET, POST, OPTIONS"))])
        def do_TRACE(self):
            if conf.get("trace"):
                echo = "".join(f"{k}: {v}\\n" for k, v in self.headers.items()).encode()
                return self._send(200, echo, [("Content-Type", "message/http")])
            return self._send(405)
    http.server.ThreadingHTTPServer(("127.0.0.1", int(os.environ["PORT"])), H).serve_forever()
    """
)

GOOD = {
    "headers": {
        "Content-Security-Policy": "default-src 'self'; frame-ancestors 'none'",
        "X-Frame-Options": "DENY",
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
    },
    "cookies": [f"sid={COOKIE_SECRET}; Path=/; HttpOnly; Secure; SameSite=Lax"],
    "cors": "own",
}
BAD = {
    "server": "Werkz/2.3.1",
    "cookies": [f"sid={COOKIE_SECRET}; Path=/"],
    "cors": "reflect",
    "trace": True,
    "traceback": True,
    "serve_git": True,
    "serve_env": True,
    "env_secret": ENV_SECRET,
    "allow": "GET, POST, TRACE",
}


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in (
        "FABRIQUE_COMMAND_DENY",
        "FABRIQUE_SANDBOX_REQUIRED",
        "FABRIQUE_SANDBOX_RUNTIME",
    ):
        monkeypatch.delenv(name, raising=False)


def _app(tmp_path, conf: dict, probe: bool = True) -> tuple[int, dict]:
    (tmp_path / "server.py").write_text(SERVER)
    (tmp_path / "conf.json").write_text(json.dumps(conf))
    port = _free_port()
    request = {
        "workspace_root": str(tmp_path),
        "start_command": f"{PY} server.py",
        "port": port,
        "ready_timeout_seconds": 20,
        "sandbox": {"mode": "host"},
        **({"security_probe": True} if probe else {}),
    }
    return port, app_run.check_application(request)


def _evidence(port: int, reply: dict) -> dict:
    return {
        "app_run": {
            "command": "server",
            "port": port,
            **reply,
            "security_probe_requested": True,
        }
    }


def _by_id(check: dict) -> dict[str, str]:
    return {item["id"].split("-", 2)[2]: item["status"] for item in check["items"]}


def _reply_schema() -> Draft202012Validator:
    doc = json.loads(SCHEMA.read_text())
    return Draft202012Validator({**doc, "$ref": "#/$defs/reply"})


# ---------------------------------------------------------------------------
# The daemon against a real application
# ---------------------------------------------------------------------------


def test_a_well_configured_application_passes_and_http_leaves_hsts_unverifiable(
    tmp_path,
):
    port, reply = _app(tmp_path, GOOD)
    probe = reply["security_probe"]
    assert probe["observed"] and probe["observed_by"] == "daemon"
    assert probe["ran_in"] == "host" and probe["scheme"] == "http"
    assert set(probe["requests"]) == {
        "get", "cors_origin", "cors_credentials", "preflight", "options",
        "trace", "not_found", "malformed", "git_head", "env_file",
    }  # fmt: skip
    assert _reply_schema().is_valid(reply), list(_reply_schema().iter_errors(reply))
    cookie = probe["requests"]["get"]["cookies"][0]
    assert cookie["httponly"] and cookie["secure"] and cookie["samesite"] == "Lax"
    assert COOKIE_SECRET not in json.dumps(reply)
    # The schema names the probe: a cookie carrying its value is refused.
    leaked = json.loads(json.dumps(reply))
    leaked["security_probe"]["requests"]["get"]["cookies"][0]["value"] = "x"
    assert not _reply_schema().is_valid(leaked)

    check = security_probe.check(_evidence(port, reply))
    assert check["outcome"] == "pass" and check["observed_by"] == "daemon"
    statuses = _by_id(check)
    assert statuses == {
        "CLNT-07": "pass",
        "CONF-04": "pass",
        "ERRH-02": "pass",
        "ERRH-01": "pass",
        "CONF-06": "pass",
        "CONF-07": "unverifiable",
        "CONF-12": "pass",
        "CONF-14": "pass",
        "CLNT-09": "pass",
        "SESS-02": "pass",
    }
    hsts = next(i for i in check["items"] if i["id"] == "WSTG-v42-CONF-07")
    assert "plain http" in hsts["observation"]
    assert "unverifiable: WSTG-v42-CONF-07" in check["observation"]


def test_a_badly_configured_application_is_refuted_with_its_findings(tmp_path):
    port, reply = _app(tmp_path, BAD)
    assert _reply_schema().is_valid(reply)
    dumped = json.dumps(reply)
    # Raw values are kept, but never a cookie's value or an env file's.
    assert COOKIE_SECRET not in dumped and ENV_SECRET not in dumped
    requests = reply["security_probe"]["requests"]
    assert requests["env_file"]["env_keys"] == ["API_TOKEN", "DEBUG"]
    assert requests["trace"]["echoed"] is True
    assert ["Server", "Werkz/2.3.1"] in requests["get"]["headers"]

    check = security_probe.check(_evidence(port, reply))
    assert check["outcome"] == "refuted"
    statuses = _by_id(check)
    assert {k for k, v in statuses.items() if v == "refuted"} == {
        "CLNT-07",
        "CONF-04",
        "ERRH-02",
    }
    assert {k for k, v in statuses.items() if v == "finding"} == {
        "ERRH-01",
        "CONF-06",
        "CONF-12",
        "CONF-14",
        "CLNT-09",
        "SESS-02",
    }
    assert statuses["CONF-07"] == "unverifiable"
    cookies = next(i for i in check["items"] if i["id"] == "WSTG-v42-SESS-02")
    assert "sid: no HttpOnly" in cookies["observation"]
    assert "no Secure over plain http" in cookies["observation"]
    assert "Access-Control-Allow-Credentials: true" in check["observation"]
    assert "a python stack trace" in check["observation"]
    assert "served the repository's HEAD" in check["observation"]


def test_a_credentialed_wildcard_is_refuted_and_a_plain_reflection_is_a_finding(
    tmp_path,
):
    port, reply = _app(tmp_path, {**GOOD, "cors": "wildcard-credentials"})
    check = security_probe.check(_evidence(port, reply))
    assert check["outcome"] == "refuted" and _by_id(check)["CLNT-07"] == "refuted"
    assert "Access-Control-Allow-Origin: *" in check["observation"]


def test_no_probe_is_sent_unless_the_request_asks(tmp_path):
    port, reply = _app(tmp_path, GOOD, probe=False)
    assert reply["listening"] and "security_probe" not in reply
    evidence = {"app_run": {"command": "server", "port": port, **reply}}
    assert security_probe.check(evidence) is None


def test_the_probe_runs_as_a_script_where_the_application_runs(tmp_path):
    """Container mode runs the module with ``python3 -c`` inside the container
    and reads back the line it prints; here the "container" is this machine."""
    (tmp_path / "server.py").write_text(SERVER)
    (tmp_path / "conf.json").write_text(json.dumps(BAD))
    port = _free_port()
    seen = []

    def run_here(argv, timeout):
        seen.append(argv[:5])
        # [runtime, "exec", name, "python3", "-c", source, url, path]
        return sandbox.run_bounded([PY, *argv[4:]], timeout)

    server = sandbox.subprocess.Popen(
        [PY, "server.py"], cwd=tmp_path, env={"PORT": str(port)}
    )
    try:
        for _ in range(100):
            if app_run.port_in_use(port):
                break
            asyncio.run(asyncio.sleep(0.05))
        probe = daemon_probe.in_container(
            "docker", "fabrique-app-x", f"http://127.0.0.1:{port}", "/", run_here
        )
    finally:
        server.kill()
        server.wait()
    assert seen == [["docker", "exec", "fabrique-app-x", "python3", "-c"]]
    assert probe["observed"] and probe["ran_in"] == "container"
    assert COOKIE_SECRET not in json.dumps(probe)
    check = security_probe.check(
        {"app_run": {"listening": True, "security_probe": probe}}
    )
    assert check["outcome"] == "refuted" and "(container)" in check["observation"]


def test_an_image_without_python_is_reported_not_observed():
    probe = daemon_probe.read(
        {"stdout": "", "stderr": "exec: python3: not found", "exit_code": 127}
    )
    assert probe["observed"] is False and "no python3" in probe["reason"]
    check = security_probe.check(
        {"app_run": {"listening": True, "security_probe": probe}}
    )
    assert check["outcome"] == "unverifiable" and "no python3" in check["observation"]


def test_what_the_container_prints_is_bounded_again():
    long = "x" * 50_000
    said = {
        "requests": {
            "get": {
                "status": 200,
                "headers": [["X-Long", long]] * 100,
                "cookies": [{"name": "a", "value": "kept?", "value_bytes": 5}],
            },
            "not_found": {"status": 404, "body_head": long},
            "trace": {"status": 200, "echoed": "yes"},
        }
    }
    probe = daemon_probe.read(
        {"stdout": daemon_probe.MARK + json.dumps(said), "exit_code": 0}
    )
    get = probe["requests"]["get"]
    assert len(get["headers"]) == daemon_probe.MAX_HEADERS
    assert len(get["headers"][0][1]) == daemon_probe.VALUE_CHARS
    assert "value" not in get["cookies"][0]
    assert (
        len(probe["requests"]["not_found"]["body_head"]) == daemon_probe.BODY_HEAD_CHARS
    )
    assert probe["requests"]["trace"]["echoed"] is False
    assert probe["requests"]["git_head"]["status"] is None


# ---------------------------------------------------------------------------
# The evidence: unverifiable is never a pass
# ---------------------------------------------------------------------------


def _probe(scheme="https", get_headers=(), cookies=()):
    entry = {
        "request": {"method": "GET", "path": "/"},
        "status": 200,
        "headers": [list(h) for h in get_headers],
        "cookies": list(cookies),
    }
    return {
        "observed": True,
        "observed_by": "daemon",
        "scheme": scheme,
        "base_url": f"{scheme}://app.test",
        "path": "/",
        "requests": {"get": entry},
    }


def test_hsts_and_secure_are_judged_only_over_https():
    over_https = security_probe.items(
        _probe(get_headers=[("Strict-Transport-Security", "max-age=31536000")])
    )
    assert {i["id"]: i["status"] for i in over_https}["WSTG-v42-CONF-07"] == "pass"
    missing = security_probe.items(_probe())
    assert {i["id"]: i["status"] for i in missing}["WSTG-v42-CONF-07"] == "finding"
    zero = security_probe.items(
        _probe(get_headers=[("Strict-Transport-Security", "max-age=0")])
    )
    assert {i["id"]: i["status"] for i in zero}["WSTG-v42-CONF-07"] == "finding"
    cookie = {"name": "sid", "httponly": True, "samesite": "Strict", "secure": False}
    https_cookie = security_probe.items(_probe(cookies=[cookie]))
    assert {i["id"]: i["status"] for i in https_cookie}["WSTG-v42-SESS-02"] == "finding"
    http_cookie = security_probe.items(_probe(scheme="http", cookies=[cookie]))
    statuses = {i["id"]: i["status"] for i in http_cookie}
    assert statuses["WSTG-v42-SESS-02"] == "unverifiable"
    assert statuses["WSTG-v42-CONF-07"] == "unverifiable"
    # Requests that got no answer are unverifiable, never a pass.
    assert {
        i["status"] for i in http_cookie if i["id"].startswith("WSTG-v42-ERRH")
    } == {"unverifiable"}


def test_the_wstg_identifiers_are_versioned():
    ids = [wstg_id for wstg_id, _ in security_probe.WSTG.values()]
    assert all(i.startswith(("WSTG-v42-", "WSTG-87cf4a4-")) for i in ids)
    # CONF-12 and CONF-14 are not in release 4.2: they carry the guide's commit.
    assert security_probe.WSTG["CONF-12"][0] == "WSTG-87cf4a4-CONF-12"
    assert security_probe.WSTG["CONF-14"][0] == "WSTG-87cf4a4-CONF-14"


def test_a_probe_asked_for_and_not_answered_is_unverifiable():
    app = {"listening": True, "security_probe_requested": True}
    check = security_probe.check({"app_run": app})
    assert check["outcome"] == "unverifiable" and check["items"] == []
    assert "did not answer" in check["observation"]
    assert security_probe.check({"app_run": {"listening": False}}) is None
    assert security_probe.check({"app_run": {"listening": True}}) is None
    assert security_probe.check(None) is None


def test_no_probe_value_is_taken_from_the_producer_s_report(sqlite_session):
    """The producer's own "probe" in its claims is a claim: the check reads only
    the daemon's reply, so a clean report cannot hide what the daemon saw, and
    a report with no observation behind it is unverifiable, never a pass."""
    company = Company(company_name="Probe Co")
    sqlite_session.add(company)
    sqlite_session.commit()
    clean = _probe(scheme="http")
    claims = {"summary": "CORS locked down", "security_probe": clean}

    def verify(evidence, ref):
        run = AgentRun(
            company_id=company.id,
            external_ref=ref,
            producer="test",
            claims=claims,
            artifact_text="x",
            artifact_digest="0" * 64,
            artifact_size=1,
        )
        sqlite_session.add(run)
        sqlite_session.commit()
        verification = verify_agent_run(sqlite_session, run, evidence)
        return verification, next(
            c for c in verification.checks if c["name"] == security_probe.CHECK_NAME
        )

    bad = {
        "requests": {
            "cors_credentials": {
                "request": {"method": "GET", "path": "/"},
                "status": 200,
                "headers": [
                    ["Access-Control-Allow-Origin", "https://evil.example"],
                    ["Access-Control-Allow-Credentials", "true"],
                ],
            }
        },
        "observed": True,
        "scheme": "http",
    }
    verification, check = verify(
        {"app_run": {"listening": True, "security_probe": bad}}, "probe-claims-1"
    )
    assert verification.verdict == "refuted" and check["outcome"] == "refuted"

    verification, check = verify(
        {"app_run": {"listening": True, "security_probe_requested": True}},
        "probe-claims-2",
    )
    assert check["outcome"] == "unverifiable" and check["items"] == []
    assert verification.verdict == "unverifiable"


def test_a_refuted_probe_sends_the_task_back_to_the_developer():
    problem = delivery_gate._GATE_PROBLEMS[security_probe.CHECK_NAME]
    assert problem == "security probe refuted"
    assert problem in delivery_gate.CODE_PROBLEMS


def test_the_loop_asks_for_the_probe_and_waits_for_it(monkeypatch):
    monkeypatch.setattr(settings, "DELIVERY_APP_CHECK_ENABLED", True)
    monkeypatch.setattr(settings, "DELIVERY_APP_READY_TIMEOUT_SECONDS", 60)
    sent = {}

    class _Nats:
        async def request(self, subject, payload, timeout=5.0):
            sent.update(payload=payload, timeout=timeout)
            return {"started": True, "listening": True, "security_probe": {"x": 1}}

    evidence = {"workspace_root": "/p"}
    ctx = project_profile.Context(_Nats(), {}, evidence, None)
    ctx._company = {"id": None, "overrides": {}, "installs": {}}
    ctx._profile = stack_profiles.resolve(
        {"stack": "python", "start": "python -m app", "port": 8000}, {}
    )
    monkeypatch.setattr(
        "services.workspace.sandbox.request_for_sprint", lambda *_a: {"mode": "host"}
    )
    asyncio.run(delivery_checks.check_application(ctx))
    assert sent["payload"]["security_probe"] is True
    assert sent["timeout"] >= 60 + daemon_probe.PROBE_SECONDS
    assert evidence["app_run"]["security_probe"] == {"x": 1}
    assert evidence["app_run"]["security_probe_requested"] is True


def test_the_bench_s_seeded_insecure_cors_is_refuted_by_the_probe():
    """``bench/verifier``: the lie is refuted by this check, the control passes it."""
    from verifier.cases import CASES

    cases = {case.name: case for case in CASES}
    lie = cases["cors_locked_down_but_any_origin_reads_with_credentials"]
    check = security_probe.check(lie.evidence)
    assert check["outcome"] == "refuted" and _by_id(check)["CLNT-07"] == "refuted"
    assert "security_probe" in lie.claims  # its own report, never read
    control = cases["honest_service_whose_probe_finds_no_weakness"]
    assert security_probe.check(control.evidence)["outcome"] == "pass"
