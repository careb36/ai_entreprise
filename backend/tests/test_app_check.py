"""The execution environment around the delivery tests (issue #315): stack
profiles detected per stack and overridable (``services/stack_profiles.py``),
the dependencies installed only when a manifest the daemon wrote changed,
and the application started in the sandbox, probed and stopped by the
daemon (``vscode-daemon/app_run.py``, ``services/app_check.py``), each a
Verifier check read from the daemon's report; a failure sends the task back.
Real commands in ``tmp_path``: a tiny ``http.server`` application."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import socket
import stat
import sys
import textwrap

import app_run  # the daemon's module, on sys.path via conftest
import pytest
import shell_exec
from config import settings
from models.agent_run import AgentRun, Verification
from models.company import Company, ProjectDiscovery
from models.sprint import BacklogItem, Sprint
from services import app_check, delivery_gate, stack_profiles, task_worker
from sqlmodel import select
from test_delivery_tests import _load_daemon
from test_git_workflow import _member, enforcement  # noqa: F401
from test_specifications import _company

PY = sys.executable
SERVE = f"{PY} -m http.server $PORT --bind 127.0.0.1"


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
    for name in (
        "DELIVERY_INSTALL_COMMAND",
        "DELIVERY_LINT_COMMAND",
        "DELIVERY_START_COMMAND",
        "DELIVERY_E2E_COMMAND",
        "DELIVERY_TEST_COMMAND",
    ):
        monkeypatch.setattr(settings, name, "")
    monkeypatch.setattr(settings, "DELIVERY_APP_PORT", 0)
    monkeypatch.setattr(settings, "DELIVERY_APP_PROBE_PATH", "/")
    monkeypatch.setattr(settings, "DELIVERY_SANDBOX", "host")


# ---------------------------------------------------------------------------
# Stack profiles
# ---------------------------------------------------------------------------


def _files(root, names: dict[str, str]) -> str:
    for name, text in names.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return str(root)


@pytest.mark.parametrize(
    ("files", "stack", "install", "start", "port"),
    [
        (
            {"package.json": json.dumps({"scripts": {"start": "node s.js"}})},
            "node",
            "npm install --no-audit --no-fund",
            "npm start",
            3000,
        ),
        (
            {"package.json": "{}", "package-lock.json": "{}"},
            "node",
            "npm ci --no-audit --no-fund",
            None,
            3000,
        ),
        (
            {"requirements.txt": "flask==3.0.0\n", "app.py": ""},
            "python",
            "python -m pip install -r requirements.txt",
            "python app.py",
            8000,
        ),
        (
            {"pyproject.toml": "[tool.poetry]\nname='x'\n", "manage.py": ""},
            "python",
            "poetry install",
            "python manage.py runserver 0.0.0.0:$PORT --noreload",
            8000,
        ),
        ({"go.mod": "module x\n"}, "go", "go mod download", "go run .", 8080),
        ({"Cargo.toml": "[package]\n"}, "rust", "cargo fetch", "cargo run", 8080),
        (
            {"pom.xml": "<project/>"},
            "java-maven",
            "mvn -q dependency:resolve",
            "mvn -q spring-boot:run",
            8080,
        ),
        (
            {"build.gradle.kts": ""},
            "java-gradle",
            "gradle testClasses",
            "gradle run",
            8080,
        ),
        (
            {"Api.csproj": "<Project/>"},
            "dotnet",
            "dotnet restore",
            "dotnet run --urls http://0.0.0.0:$PORT",
            5000,
        ),
        (
            {"composer.json": "{}", "public/index.php": ""},
            "php",
            "composer install --no-interaction",
            "php -S 0.0.0.0:$PORT -t public",
            8000,
        ),
        (
            {"Gemfile": "", "config.ru": ""},
            "ruby",
            "bundle install",
            "bundle exec rackup -o 0.0.0.0 -p $PORT",
            9292,
        ),
        (
            {"Gemfile": "", "bin/rails": ""},
            "ruby",
            "bundle install",
            "bin/rails server -b 0.0.0.0 -p $PORT",
            3000,
        ),
    ],
)
def test_each_stack_is_detected_with_its_commands(
    tmp_path, files, stack, install, start, port
):
    profile = stack_profiles.detect(_files(tmp_path, files))
    assert profile["stack"] == stack
    assert profile["install"] == install
    assert profile["start"] == start
    assert profile["port"] == port
    assert profile["test"] and profile["probe_path"] == "/"


def test_node_e2e_and_lint_scripts_come_from_package_json(tmp_path):
    scripts = {"start": "node s.js", "lint": "eslint .", "test:e2e": "playwright"}
    profile = stack_profiles.detect(
        _files(tmp_path, {"package.json": json.dumps({"scripts": scripts})})
    )
    assert profile["e2e"] == "npm run test:e2e --silent"
    assert profile["lint"] == "npm run lint --if-present"


def test_an_unknown_project_has_no_profile(tmp_path):
    assert stack_profiles.detect(_files(tmp_path, {"README.md": "hi"})) is None


def test_manifests_are_recognised_at_any_depth():
    for path in (
        "package.json",
        "web/package.json",
        "requirements-dev.txt",
        "Api/Api.csproj",
        "gradle/libs.versions.toml",
        "go.sum",
    ):
        assert stack_profiles.is_manifest(path), path
    for path in ("README.md", "src/app.py", "libs.versions.toml", ""):
        assert not stack_profiles.is_manifest(path), path


def test_overrides_deployment_then_company_and_empty_turns_a_step_off(
    tmp_path, monkeypatch
):
    detected = stack_profiles.detect(
        _files(tmp_path, {"requirements.txt": "", "app.py": ""})
    )
    monkeypatch.setattr(settings, "DELIVERY_START_COMMAND", "gunicorn app:app")
    monkeypatch.setattr(settings, "DELIVERY_APP_PORT", 9000)
    profile = stack_profiles.resolve(detected, {"port": 9100, "install": ""})
    assert profile["start"] == "gunicorn app:app"
    assert profile["port"] == 9100
    assert profile["install"] is None
    assert profile["source"]["start"] == "deployment"
    assert profile["source"]["port"] == "company"
    assert profile["source"]["test"] == "detected"
    monkeypatch.setattr(settings, "DELIVERY_START_COMMAND", "")
    switched = stack_profiles.resolve(detected, {"stack": "go"})
    assert switched["stack"] == "go" and switched["start"] == "go run ."


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        ({"colour": "x"}, "unknown stack profile field"),
        ({"port": "80"}, "port must be an integer"),
        ({"port": 70000}, "between 1 and 65535"),
        ({"start": "a\nb"}, "one line"),
        ({"probe_path": "health"}, "must start with /"),
        ({"stack": "cobol"}, "unknown stack"),
    ],
)
def test_bad_overrides_are_refused_with_the_reason(raw, reason):
    with pytest.raises(ValueError, match=reason):
        stack_profiles.validate_overrides(raw)


# ---------------------------------------------------------------------------
# Install only when a manifest changed
# ---------------------------------------------------------------------------


def _write(path, sha, status="written"):
    return {"path": path, "status": status, "sha256": sha}


def test_only_a_written_manifest_whose_hash_changed_calls_for_an_install():
    evidence = {
        "workspace_root": "/p",
        "workspace_writes": [
            _write("requirements.txt", "aaa"),
            _write("package.json", "bbb"),
            _write("src/app.py", "ccc"),
            _write("go.mod", "ddd", status="rejected"),
        ],
    }
    installs = {"/p": {"package.json": "bbb"}, "/other": {"requirements.txt": "aaa"}}
    changed = app_check.changed_manifests(evidence, installs)
    assert changed == [{"path": "requirements.txt", "sha256": "aaa", "previous": None}]
    assert (
        app_check.changed_manifests(
            evidence, {"/p": {"requirements.txt": "aaa", "package.json": "bbb"}}
        )
        == []
    )


class _Daemon:
    """The real daemon's handlers behind the NATS calls the backend makes."""

    jetstream_enabled = False

    def __init__(self):
        self.daemon = _load_daemon()
        self.requests: list[tuple[str, dict]] = []

    async def request(self, subject, payload, timeout=5.0):
        self.requests.append((subject, payload))
        if subject == "workspace.generate":
            return {
                "ok": True,
                "files": self.daemon.write_files(
                    payload["workspace_root"], payload["files"]
                ),
            }
        if subject == "workspace.execute":
            return shell_exec.execute_shell_command(
                payload["command"],
                payload["workspace_root"],
                payload.get("sandbox"),
                payload.get("command_policy"),
                payload.get("timeout_seconds"),
            )
        if subject == "workspace.inspect":
            return self.daemon.inspect_project(payload["path"], payload["snapshot"])
        if subject == "workspace.app.check":
            return await asyncio.to_thread(app_run.check_application, payload)
        raise AssertionError(subject)

    async def publish(self, subject, payload, msg_id=None):
        pass

    def subjects(self):
        return [subject for subject, _payload in self.requests]


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _install_evidence(root, text):
    (root / "requirements.txt").write_text(text)
    return {
        "workspace_root": str(root),
        "workspace_writes": [_write("requirements.txt", _sha(text))],
    }


def test_install_runs_when_the_manifest_changed_and_not_again_for_the_same_bytes(
    sqlite_session, tmp_path, monkeypatch
):
    company = _company(sqlite_session)
    root = tmp_path / "project"
    root.mkdir()
    monkeypatch.setattr(settings, "DELIVERY_INSTALL_NETWORK", True)
    monkeypatch.setattr(
        settings,
        "DELIVERY_INSTALL_COMMAND",
        f"{PY} -c \"open('installs.log', 'a').write('x')\"",
    )
    daemon = _Daemon()
    sprint = {"id": None, "company_id": company.id}
    bind = sqlite_session.get_bind()

    def install(text):
        evidence = _install_evidence(root, text)
        ctx = app_check.Context(daemon, sprint, evidence, bind)
        asyncio.run(app_check.install_if_needed(ctx))
        return evidence

    first = install("flask==3.0.0\n")
    record = first["dependency_install"]
    assert (
        record["exit_code"] == 0
        and record["manifests"][0]["path"] == "requirements.txt"
    )
    assert record["source"] == "deployment"
    assert record["sandbox"]["mode"] == "host"
    _subject, payload = daemon.requests[-1]
    assert payload["sandbox"]["network"] is True
    assert payload["timeout_seconds"] == settings.DELIVERY_INSTALL_TIMEOUT_SECONDS
    check = app_check.check_install(first)
    assert (
        check["outcome"] == "pass"
        and "requirements.txt changed" in check["observation"]
    )
    sqlite_session.expire_all()
    installed = sqlite_session.get(Company, company.id).dependency_installs
    assert installed == {str(root): {"requirements.txt": _sha("flask==3.0.0\n")}}

    again = install("flask==3.0.0\n")
    assert "dependency_install" not in again
    assert app_check.checks(again) == []
    changed = install("flask==3.1.0\n")
    assert changed["dependency_install"]["manifests"][0]["previous"] == _sha(
        "flask==3.0.0\n"
    )
    assert (root / "installs.log").read_text() == "xx"


def test_a_failed_install_is_refuted_and_retried_next_time(
    sqlite_session, tmp_path, monkeypatch
):
    company = _company(sqlite_session)
    root = tmp_path / "project"
    root.mkdir()
    monkeypatch.setattr(
        settings,
        "DELIVERY_INSTALL_COMMAND",
        f"{PY} -c \"import sys; print('No matching distribution'); sys.exit(1)\"",
    )
    daemon = _Daemon()
    bind = sqlite_session.get_bind()
    evidence = _install_evidence(root, "nopackage==9\n")
    asyncio.run(
        app_check.install_if_needed(
            app_check.Context(daemon, {"company_id": company.id}, evidence, bind)
        )
    )
    check = app_check.check_install(evidence)
    assert check["outcome"] == "refuted"
    assert (
        "exited 1" in check["observation"]
        and "No matching distribution" in check["observation"]
    )
    sqlite_session.expire_all()
    assert not sqlite_session.get(Company, company.id).dependency_installs
    again = _install_evidence(root, "nopackage==9\n")
    asyncio.run(
        app_check.install_if_needed(
            app_check.Context(daemon, {"company_id": company.id}, again, bind)
        )
    )
    assert again["dependency_install"]["exit_code"] == 1


def test_install_is_off_when_disabled(sqlite_session, tmp_path, monkeypatch):
    company = _company(sqlite_session)
    monkeypatch.setattr(settings, "DELIVERY_INSTALL_ENABLED", False)
    daemon = _Daemon()
    evidence = _install_evidence(tmp_path, "flask\n")
    asyncio.run(
        app_check.install_if_needed(
            app_check.Context(
                daemon, {"company_id": company.id}, evidence, sqlite_session.get_bind()
            )
        )
    )
    assert "dependency_install" not in evidence and daemon.requests == []


def test_the_daemon_bounds_a_requested_timeout():
    assert shell_exec.bounded_timeout(None) == shell_exec.COMMAND_TIMEOUT_SECONDS
    assert shell_exec.bounded_timeout(5000) == shell_exec.MAX_COMMAND_TIMEOUT_SECONDS
    assert shell_exec.bounded_timeout("0") == 1
    hung = shell_exec.execute_shell_command(
        f'{PY} -c "import time; time.sleep(5)"', ".", timeout=1
    )
    assert hung["timed_out"] and hung["timeout_seconds"] == 1


# ---------------------------------------------------------------------------
# The application started, probed and stopped by the daemon
# ---------------------------------------------------------------------------


def _check(tmp_path, start=SERVE, **extra):
    port = extra.pop("port", None) or _free_port()
    request = {
        "workspace_root": str(tmp_path),
        "start_command": start,
        "port": port,
        "ready_timeout_seconds": extra.pop("ready", 20),
        "sandbox": {"mode": "host"},
        **extra,
    }
    return port, app_run.check_application(request)


SLOW_TO_STOP = (
    "import http.server, signal, sys, time\n"
    "def bye(*_):\n"
    "    time.sleep(1)\n"
    "    sys.exit(0)\n"
    "signal.signal(signal.SIGTERM, bye)\n"
    "http.server.test(HandlerClass=http.server.SimpleHTTPRequestHandler,"
    " port=int(sys.argv[1]), bind='127.0.0.1')\n"
)


def test_a_server_the_shell_outlives_is_stopped_before_the_reply(tmp_path):
    """The shell dies at once; the server it started takes a second to let go."""
    (tmp_path / "index.html").write_text("hello")
    (tmp_path / "slow.py").write_text(SLOW_TO_STOP)
    port, reply = _check(tmp_path, start=f"{PY} slow.py $PORT; true")
    assert reply["listening"] and reply["stopped"]
    assert not app_run.port_in_use(port)


def test_the_application_is_started_probed_and_stopped(tmp_path):
    (tmp_path / "index.html").write_text("hello")
    port, reply = _check(tmp_path)
    assert reply["started"] and reply["listening"] and reply["stopped"]
    assert reply["probe"] == {"url": f"http://127.0.0.1:{port}/", "status": 200}
    assert reply["sandbox"]["mode"] == "host" and reply["sandbox"]["isolated"] is False
    assert "GET / " in reply["log"]["output_tail"]
    assert not app_run.port_in_use(port)
    check = app_check.check_app({"app_run": {"command": SERVE, "port": port, **reply}})
    assert check["outcome"] == "pass"
    assert "answered 200" in check["observation"]


def test_an_application_that_exits_before_listening_is_refuted_with_its_log(tmp_path):
    start = f"{PY} -c \"import sys; print('ImportError: no module named flask'); sys.exit(3)\""
    port, reply = _check(tmp_path, start=start)
    assert reply["started"] and not reply["listening"] and reply["exit_code"] == 3
    check = app_check.check_app({"app_run": {"command": start, "port": port, **reply}})
    assert check["outcome"] == "refuted"
    assert "exited 3 before listening" in check["observation"]
    assert "no module named flask" in check["observation"]


def test_an_application_that_never_listens_is_stopped_at_the_bound(tmp_path):
    start = f'{PY} -c "import time; time.sleep(60)"'
    port, reply = _check(tmp_path, start=start, ready=1)
    assert reply["started"] and not reply["listening"] and reply["stopped"]
    assert reply.get("exit_code") is None
    check = app_check.check_app({"app_run": {"command": start, "port": port, **reply}})
    assert (
        check["outcome"] == "refuted"
        and "did not listen within 1s" in check["observation"]
    )


def test_a_server_error_on_the_probe_is_refuted(tmp_path):
    (tmp_path / "server.py").write_text(
        textwrap.dedent(
            """
            import http.server, os
            class H(http.server.BaseHTTPRequestHandler):
                def do_GET(self):
                    self.send_response(500); self.end_headers()
            http.server.HTTPServer(("127.0.0.1", int(os.environ["PORT"])), H).serve_forever()
            """
        )
    )
    port, reply = _check(tmp_path, start=f"{PY} server.py")
    assert reply["probe"]["status"] == 500
    check = app_check.check_app({"app_run": {"command": "x", "port": port, **reply}})
    assert check["outcome"] == "refuted" and "answered 500" in check["observation"]


def test_the_e2e_command_runs_against_the_running_application(tmp_path):
    ok = f"{PY} -c \"import os, urllib.request; urllib.request.urlopen(os.environ['APP_URL'])\""
    port, reply = _check(tmp_path, e2e_command=ok)
    assert reply["e2e"]["exit_code"] == 0
    evidence = {"app_run": {"command": SERVE, "port": port, **reply}}
    assert app_check.check_e2e(evidence)["outcome"] == "pass"
    failing = f"{PY} -c \"import sys; print('1 failed'); sys.exit(1)\""
    port, reply = _check(tmp_path, e2e_command=failing)
    evidence = {"app_run": {"command": SERVE, "port": port, **reply}}
    check = app_check.check_e2e(evidence)
    assert check["outcome"] == "refuted" and "1 failed" in check["observation"]


def test_the_command_policy_refuses_before_anything_runs(tmp_path, monkeypatch):
    monkeypatch.setenv("FABRIQUE_COMMAND_DENY", "http.server")
    port, reply = _check(tmp_path)
    assert reply["refused"] and "started" not in reply
    assert not app_run.port_in_use(port)
    check = app_check.check_app(
        {"app_run": {"command": SERVE, "port": port, **app_check._app_record(reply)}}
    )
    assert check["outcome"] == "unverifiable"


def test_a_port_already_in_use_is_not_probed(tmp_path):
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        port = busy.getsockname()[1]
        _port, reply = _check(tmp_path, port=port)
    assert "already in use" in reply["error"] and "started" not in reply


def test_a_required_sandbox_is_never_replaced_by_the_host(tmp_path, monkeypatch):
    monkeypatch.setenv("FABRIQUE_SANDBOX_REQUIRED", "bwrap")
    _port, reply = _check(tmp_path)
    assert "sandbox unavailable" in reply["error"] and "started" not in reply
    monkeypatch.delenv("FABRIQUE_SANDBOX_REQUIRED")
    monkeypatch.setenv("FABRIQUE_SANDBOX_RUNTIME", "no-such-runtime")
    reply = app_run.check_application(
        {
            "workspace_root": str(tmp_path),
            "start_command": SERVE,
            "port": _free_port(),
            "sandbox": {"mode": "container"},
        }
    )
    assert "sandbox unavailable" in reply["error"]


@pytest.fixture
def fake_docker(tmp_path, monkeypatch):
    """A ``docker`` that runs a detached container's command on the host,
    in the mounted folder, and answers ``inspect``, ``exec``, ``logs`` and
    ``rm`` about it: the argument shape without a container runtime."""
    state = tmp_path / "docker-state"
    state.mkdir()
    script = tmp_path / "bin" / "docker"
    script.parent.mkdir()
    script.write_text(
        f"#!{PY}\n"
        + textwrap.dedent(
            f"""
            import os, signal, subprocess, sys
            state = {str(state)!r}
            args = sys.argv[1:]
            open(os.path.join(state, "calls.log"), "a").write(" ".join(args) + "\\n")
            def envs(items):
                out = dict(os.environ)
                for i, a in enumerate(items):
                    if a == "-e":
                        k, _, v = items[i + 1].partition("=")
                        out[k] = v
                return out
            if args[0] == "run":
                name = args[args.index("--name") + 1]
                src = args[args.index("-v") + 1].split(":/workspace")[0]
                command = args[-1]
                log = open(os.path.join(state, name + ".log"), "w")
                exit_file = os.path.join(state, name + ".exit")
                p = subprocess.Popen(["/bin/sh", "-c", command + '; echo $? > ' + exit_file],
                    cwd=src, env=envs(args), stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
                open(os.path.join(state, name + ".pid"), "w").write(str(p.pid))
                open(os.path.join(state, name + ".src"), "w").write(src)
                print(name)
            elif args[0] == "inspect":
                name = args[-1]
                exit_file = os.path.join(state, name + ".exit")
                if os.path.exists(exit_file):
                    print("false", open(exit_file).read().strip())
                else:
                    print("true 0")
            elif args[0] == "exec":
                name = args[-4]
                src = open(os.path.join(state, name + ".src")).read()
                sys.exit(subprocess.run(args[-3:], cwd=src, env=envs(args)).returncode)
            elif args[0] == "logs":
                sys.stdout.write(open(os.path.join(state, args[-1] + ".log")).read())
            elif args[0] in ("rm", "kill"):
                pid = int(open(os.path.join(state, args[-1] + ".pid")).read())
                try:
                    os.killpg(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            """
        )
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{script.parent}{os.pathsep}{os.environ['PATH']}")
    return state / "calls.log"


def test_container_mode_runs_the_application_isolated_and_probes_it_inside(
    tmp_path, fake_docker
):
    work = tmp_path / "work"
    work.mkdir()
    (work / "index.html").write_text("hello")
    port = _free_port()
    reply = app_run.check_application(
        {
            "workspace_root": str(work),
            "start_command": SERVE,
            "port": port,
            "ready_timeout_seconds": 20,
            "sandbox": {"mode": "container", "image": "python:3.12-slim"},
        }
    )
    assert reply["started"] and reply["listening"] and reply["stopped"], reply
    assert reply["probe"]["status"] == 200
    assert reply["sandbox"]["mode"] == "container" and reply["sandbox"]["isolated"]
    calls = fake_docker.read_text()
    run = next(line for line in calls.splitlines() if line.startswith("run "))
    assert " -d " in run and "--network none" in run and "--cap-drop ALL" in run
    assert f"PORT={port}" in run and "--rm" not in run
    assert any(line.startswith("rm -f ") for line in calls.splitlines())


# ---------------------------------------------------------------------------
# Through the task worker: the checks recorded, a failure sent back
# ---------------------------------------------------------------------------


@pytest.fixture()
def project(monkeypatch, sqlite_session, tmp_path):
    bind = sqlite_session.get_bind()
    ProjectDiscovery.__table__.create(bind, checkfirst=True)
    monkeypatch.setattr(task_worker, "engine", bind)
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "audit"))
    monkeypatch.setattr(settings, "DELIVERY_TESTS_ENABLED", True)
    monkeypatch.setattr(settings, "DELIVERY_APP_CHECK_ENABLED", True)
    monkeypatch.setattr(
        settings, "DELIVERY_TEST_COMMAND", f"{PY} -c \"print('1 passed')\""
    )
    monkeypatch.setattr(
        settings,
        "DELIVERY_INSTALL_COMMAND",
        f"{PY} -c \"open('installed.txt', 'a').write('x')\"",
    )
    root = tmp_path / "project"
    root.mkdir()
    port = _free_port()
    company = Company(
        company_name=f"Run Corp {tmp_path.name}",
        autonomy_level=2,
        stack_profile={"start": f"{PY} app.py", "port": port},
    )
    sqlite_session.add(company)
    sqlite_session.commit()
    sqlite_session.refresh(company)
    sqlite_session.add(ProjectDiscovery(company_id=company.id, project_path=str(root)))
    sprint = Sprint(company_id=company.id, sprint_name="T", scope_size="S")
    sqlite_session.add(sprint)
    sqlite_session.commit()
    sqlite_session.refresh(sprint)
    item = BacklogItem(sprint_id=sprint.id, title="Serve", status="in_progress")
    sqlite_session.add(item)
    sqlite_session.commit()
    sqlite_session.refresh(item)
    return (
        sqlite_session,
        root,
        {"id": item.id, "title": item.title},
        {"role": "Developer", "served_model": "m"},
        {"id": sprint.id, "company_id": company.id},
    )


_GOOD_APP = """import http.server, os
http.server.HTTPServer(("127.0.0.1", int(os.environ["PORT"])), http.server.SimpleHTTPRequestHandler).serve_forever()
"""
_BROKEN_APP = """import flask_that_is_not_installed
"""


def _answer(app: str) -> str:
    return (
        "Serves the site.\n\n"
        f"```python path=app.py\n{app}```\n\n"
        "```text path=requirements.txt\nrequests==2.32.3\n```\n"
    )


def _checks(session, company_id, item_id):
    session.expire_all()
    run = session.exec(
        select(AgentRun).where(
            AgentRun.company_id == company_id,
            AgentRun.external_ref == f"task-{item_id}",
        )
    ).first()
    verification = session.exec(
        select(Verification)
        .where(Verification.agent_run_id == run.id)
        .order_by(Verification.id.desc())
    ).first()
    return verification, {c["name"]: c for c in verification.checks}


def test_a_delivery_installs_runs_and_probes_the_application(project):
    session, root, item, agent, sprint = project
    daemon = _Daemon()
    asyncio.run(
        task_worker._deliver_artifacts(daemon, item, agent, sprint, _answer(_GOOD_APP))
    )

    verification, checks = _checks(session, sprint["company_id"], item["id"])
    subjects = daemon.subjects()
    assert subjects.index("workspace.app.check") > subjects.index("workspace.execute")
    assert checks["dependencies_installed"]["outcome"] == "pass"
    assert checks["tests_run"]["outcome"] == "pass"
    assert checks["application_started"]["outcome"] == "pass", checks[
        "application_started"
    ]
    assert "e2e_run" not in checks
    assert (root / "installed.txt").read_text() == "x"
    assert verification.evidence["app_run"]["source"] == "company"
    assert verification.verdict == "verified"
    assert (
        delivery_gate.gate(session.get_bind(), sprint["company_id"], item["id"]) is None
    )


def test_a_delivery_whose_application_fails_to_start_is_sent_back(project):
    session, _root, item, agent, sprint = project
    asyncio.run(
        task_worker._deliver_artifacts(
            _Daemon(), item, agent, sprint, _answer(_BROKEN_APP)
        )
    )

    verification, checks = _checks(session, sprint["company_id"], item["id"])
    started = checks["application_started"]
    assert started["outcome"] == "refuted"
    assert "before listening" in started["observation"]
    assert "flask_that_is_not_installed" in started["observation"]
    assert verification.verdict == "refuted"
    problem, observation = delivery_gate.gate(
        session.get_bind(), sprint["company_id"], item["id"]
    )
    assert (
        problem == "application failed to start"
        and problem in delivery_gate.CODE_PROBLEMS
    )
    assert "before listening" in observation


def test_the_application_is_not_started_when_the_tests_failed(project, monkeypatch):
    session, _root, item, agent, sprint = project
    monkeypatch.setattr(
        settings, "DELIVERY_TEST_COMMAND", f'{PY} -c "import sys; sys.exit(1)"'
    )
    daemon = _Daemon()
    asyncio.run(
        task_worker._deliver_artifacts(daemon, item, agent, sprint, _answer(_GOOD_APP))
    )
    verification, checks = _checks(session, sprint["company_id"], item["id"])
    assert "workspace.app.check" not in daemon.subjects()
    assert "application_started" not in checks
    assert verification.evidence["app_run"]["skipped"] == "the tests failed"


def test_the_application_check_is_opt_in(project, monkeypatch):
    session, _root, item, agent, sprint = project
    monkeypatch.setattr(settings, "DELIVERY_APP_CHECK_ENABLED", False)
    daemon = _Daemon()
    asyncio.run(
        task_worker._deliver_artifacts(daemon, item, agent, sprint, _answer(_GOOD_APP))
    )
    verification, checks = _checks(session, sprint["company_id"], item["id"])
    assert "workspace.app.check" not in daemon.subjects()
    assert (
        "application_started" not in checks and "app_run" not in verification.evidence
    )
    assert checks["dependencies_installed"]["outcome"] == "pass"


def test_a_failed_install_sends_the_task_back(project, monkeypatch):
    session, _root, item, agent, sprint = project
    monkeypatch.setattr(
        settings, "DELIVERY_INSTALL_COMMAND", f'{PY} -c "import sys; sys.exit(2)"'
    )
    daemon = _Daemon()
    asyncio.run(
        task_worker._deliver_artifacts(daemon, item, agent, sprint, _answer(_GOOD_APP))
    )
    _verification, checks = _checks(session, sprint["company_id"], item["id"])
    assert checks["dependencies_installed"]["outcome"] == "refuted"
    assert "workspace.app.check" not in daemon.subjects()
    problem, _obs = delivery_gate.gate(
        session.get_bind(), sprint["company_id"], item["id"]
    )
    assert problem == "dependency install failed"


def test_a_company_can_turn_its_tests_off_in_its_profile(project):
    session, _root, item, agent, sprint = project
    company = session.get(Company, sprint["company_id"])
    company.stack_profile = {**company.stack_profile, "test": ""}
    session.add(company)
    session.commit()
    asyncio.run(
        task_worker._deliver_artifacts(
            _Daemon(), item, agent, sprint, _answer(_GOOD_APP)
        )
    )
    verification, checks = _checks(session, sprint["company_id"], item["id"])
    assert (
        verification.evidence["test_run"]["reason"]
        == "tests turned off by the company's stack profile"
    )
    assert checks["tests_run"]["outcome"] == "unverifiable"


# ---------------------------------------------------------------------------
# The company's profile
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("enforcement")
def test_a_manager_sets_the_stack_profile_and_a_viewer_reads_it(
    sqlite_session, test_client
):
    company, other = _company(sqlite_session), _company(sqlite_session)
    manager = _member(sqlite_session, company.id, "Manager")
    viewer = _member(sqlite_session, company.id, "Viewer")
    outsider = _member(sqlite_session, other.id, "Manager")
    url = f"/v1/companies/{company.id}/stack_profile"
    read = test_client.get(url, headers=viewer)
    assert read.status_code == 200, read.text
    body = read.json()
    assert body["overrides"] == {} and {s["stack"] for s in body["stacks"]} >= {
        "node",
        "python",
        "dotnet",
        "php",
    }
    assert test_client.get(url, headers=outsider).status_code == 403
    assert (
        test_client.put(
            url, json={"profile": {"port": 8080}}, headers=viewer
        ).status_code
        == 403
    )
    bad = test_client.put(url, json={"profile": {"port": 0}}, headers=manager)
    assert bad.status_code == 422 and "between 1 and 65535" in bad.text
    saved = test_client.put(
        url,
        json={
            "profile": {
                "start": "uvicorn app:app --port $PORT",
                "port": 8080,
                "e2e": "",
            }
        },
        headers=manager,
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["overrides"] == {
        "start": "uvicorn app:app --port $PORT",
        "port": 8080,
        "e2e": "",
    }
    sqlite_session.expire_all()
    assert sqlite_session.get(Company, company.id).stack_profile["port"] == 8080
    cleared = test_client.put(url, json={"profile": {}}, headers=manager).json()
    assert cleared["overrides"] == {}
