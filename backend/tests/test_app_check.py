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
import worker_stages
from config import settings
from factories import company as _company
from factories import enforcement  # noqa: F401 - the fixture, by name
from factories import member_headers as _member
from models.agent_run import AgentRun, Verification
from models.company import Company, ProjectDiscovery
from models.sprint import BacklogItem, Sprint
from services.evidence import app_check, delivery_gate
from services.loop import delivery_checks
from services.loop.worker import deliver as worker_deliver
from services.workspace import (
    delivery_commands,
    dependency_install,
    project_profile,
    stack_profiles,
)
from sqlmodel import select
from test_delivery_tests import _load_daemon

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
            "poetry run python manage.py runserver 0.0.0.0:$PORT --noreload",
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
    assert profile["probe_path"] == "/"


@pytest.mark.parametrize(
    ("files", "test", "start"),
    [
        (
            {"package.json": json.dumps({"scripts": {"test": "jest"}})},
            "npm test --silent",
            None,
        ),
        (
            {
                "package.json": json.dumps(
                    {"scripts": {"test": stack_profiles.NPM_DEFAULT_TEST}}
                )
            },
            None,
            None,
        ),
        ({"package.json": "{}"}, None, None),
        ({"requirements.txt": "", "tests/test_a.py": ""}, "python -m pytest -q", None),
        ({"requirements.txt": "", "app.py": ""}, None, "python app.py"),
        (
            {
                "pyproject.toml": "[tool.poetry]\n[tool.pytest.ini_options]\n",
                "main.py": "",
            },
            "poetry run python -m pytest -q",
            "poetry run python main.py",
        ),
        (
            {"Pipfile": "", "tests/test_a.py": "", "manage.py": ""},
            "pipenv run python -m pytest -q",
            "pipenv run python manage.py runserver 0.0.0.0:$PORT --noreload",
        ),
        (
            {"Api.csproj": "<Project/>"},
            "dotnet test",
            "dotnet run --urls http://0.0.0.0:$PORT",
        ),
        (
            {"composer.json": "{}", "phpunit.xml": ""},
            "vendor/bin/phpunit",
            "php -S 0.0.0.0:$PORT",
        ),
        ({"composer.json": "{}"}, None, "php -S 0.0.0.0:$PORT"),
        ({"Gemfile": "", "Rakefile": ""}, "bundle exec rake test", None),
        ({"Gemfile": "", "spec/a_spec.rb": ""}, "bundle exec rspec", None),
        ({"Gemfile": ""}, None, None),
    ],
)
def test_the_test_and_start_commands_follow_the_project_s_suite_and_environment(
    tmp_path, files, test, start
):
    """No suite, no test command (npm's placeholder, nothing for pytest to
    collect); Poetry and Pipenv run inside their own environment."""
    profile = stack_profiles.detect(_files(tmp_path, files))
    assert profile["test"] == test
    assert profile["start"] == start
    if "Pipfile" in files:
        assert profile["install"] == "pipenv install --dev"
        assert profile["lint"] == "pipenv run python -m ruff check ."


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
        _files(tmp_path, {"requirements.txt": "", "app.py": "", "tests/test_a.py": ""})
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
    changed = dependency_install.changed_manifests(evidence, installs)
    assert changed == [{"path": "requirements.txt", "sha256": "aaa", "previous": None}]
    assert (
        dependency_install.changed_manifests(
            evidence, {"/p": {"requirements.txt": "aaa", "package.json": "bbb"}}
        )
        == []
    )


def test_only_the_manifests_the_install_covers_count_as_installed():
    node = stack_profiles.resolve(
        {
            "stack": "node",
            "install": "npm ci",
            "manifests": ["package.json", "package-lock.json"],
        },
        {},
    )
    assert stack_profiles.installed_by(node, "package.json")
    assert not stack_profiles.installed_by(node, "web/package.json")
    assert not stack_profiles.installed_by(node, "backend/requirements.txt")
    gradle = stack_profiles.template_for("java-gradle")
    gradle["source"] = {"install": "detected"}
    assert stack_profiles.installed_by(gradle, "gradle/libs.versions.toml")
    named = stack_profiles.resolve(node, {"install": "make deps"})
    assert stack_profiles.installed_by(named, "backend/requirements.txt")


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
        ctx = project_profile.Context(daemon, sprint, evidence, bind)
        asyncio.run(dependency_install.install_if_needed(ctx))
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
        dependency_install.install_if_needed(
            project_profile.Context(daemon, {"company_id": company.id}, evidence, bind)
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
        dependency_install.install_if_needed(
            project_profile.Context(daemon, {"company_id": company.id}, again, bind)
        )
    )
    assert again["dependency_install"]["exit_code"] == 1


def test_install_is_off_when_disabled(sqlite_session, tmp_path, monkeypatch):
    company = _company(sqlite_session)
    monkeypatch.setattr(settings, "DELIVERY_INSTALL_ENABLED", False)
    daemon = _Daemon()
    evidence = _install_evidence(tmp_path, "flask\n")
    asyncio.run(
        dependency_install.install_if_needed(
            project_profile.Context(
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
    record = delivery_commands.app_record(reply)
    check = app_check.check_app({"app_run": {"command": SERVE, "port": port, **record}})
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
    worker_stages.use_engine(monkeypatch, bind)
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
        worker_deliver._deliver_artifacts(
            daemon, item, agent, sprint, _answer(_GOOD_APP)
        )
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
        worker_deliver._deliver_artifacts(
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
        worker_deliver._deliver_artifacts(
            daemon, item, agent, sprint, _answer(_GOOD_APP)
        )
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
        worker_deliver._deliver_artifacts(
            daemon, item, agent, sprint, _answer(_GOOD_APP)
        )
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
        worker_deliver._deliver_artifacts(
            daemon, item, agent, sprint, _answer(_GOOD_APP)
        )
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
        worker_deliver._deliver_artifacts(
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


# ---------------------------------------------------------------------------
# Review findings: install network and coverage, the resolved test command,
# the integration's tests-only path, the reply budget and process cleanup
# ---------------------------------------------------------------------------


def _install(session, daemon, company, root, writes, profile=None):
    evidence = {"workspace_root": str(root), "workspace_writes": writes}
    ctx = project_profile.Context(
        daemon, {"company_id": company.id}, evidence, session.get_bind()
    )
    ctx._company = {"id": company.id, "overrides": {}, "installs": {}}
    if profile is not None:
        ctx._profile = profile
    return ctx, evidence


def test_install_network_follows_its_own_setting_both_ways(
    sqlite_session, tmp_path, monkeypatch
):
    company = _company(sqlite_session)
    monkeypatch.setattr(settings, "DELIVERY_INSTALL_COMMAND", f'{PY} -c "pass"')
    monkeypatch.setattr(settings, "DELIVERY_SANDBOX_NETWORK", True)
    monkeypatch.setattr(dependency_install, "_record_installed", lambda *a: None)
    for allowed in (False, True):
        monkeypatch.setattr(settings, "DELIVERY_INSTALL_NETWORK", allowed)
        daemon = _Daemon()
        ctx, _evidence = _install(
            sqlite_session, daemon, company, tmp_path, [_write("requirements.txt", "a")]
        )
        asyncio.run(dependency_install.install_if_needed(ctx))
        _subject, payload = daemon.requests[-1]
        assert payload["sandbox"]["network"] is allowed


def test_a_manifest_the_install_does_not_cover_is_never_recorded_as_installed(
    sqlite_session, tmp_path, monkeypatch
):
    """A root npm install says nothing of ``backend/requirements.txt``."""
    company = _company(sqlite_session)
    recorded = []
    monkeypatch.setattr(
        dependency_install,
        "_record_installed",
        lambda _e, _c, _r, m: recorded.append(m),
    )
    profile = stack_profiles.resolve(
        {
            "stack": "node",
            "install": f"{PY} -c \"open('ran', 'a').write('x')\"",
            "manifests": ["package.json", "package-lock.json"],
        },
        {},
    )
    writes = [_write("package.json", "p1"), _write("backend/requirements.txt", "r1")]
    ctx, evidence = _install(
        sqlite_session, _Daemon(), company, tmp_path, writes, profile
    )
    asyncio.run(dependency_install.install_if_needed(ctx))
    record = evidence["dependency_install"]
    assert record["exit_code"] == 0
    assert [m["path"] for m in record["manifests"]] == ["package.json"]
    assert [m["path"] for m in record["not_covered"]] == ["backend/requirements.txt"]
    assert [[m["path"] for m in batch] for batch in recorded] == [["package.json"]]

    daemon = _Daemon()
    only = [_write("backend/requirements.txt", "r1")]
    ctx, evidence = _install(sqlite_session, daemon, company, tmp_path, only, profile)
    asyncio.run(dependency_install.install_if_needed(ctx))
    record = evidence["dependency_install"]
    assert (
        record["command"] is None
        and "does not install backend/requirements.txt" in record["reason"]
    )
    assert daemon.requests == [] and len(recorded) == 1
    assert app_check.check_install(evidence)["outcome"] == "unverifiable"
    assert (tmp_path / "ran").read_text() == "x"


def _test_ctx(root, overrides=None):
    evidence = {"workspace_root": str(root)}
    ctx = project_profile.Context(_Daemon(), {}, evidence, None)
    ctx._company = {"id": None, "overrides": overrides or {}, "installs": {}}
    return ctx


def test_the_delivery_tests_run_the_resolved_profile_s_command(tmp_path):
    php = tmp_path / "php"
    _files(php, {"composer.json": "{}", "phpunit.xml": ""})
    assert asyncio.run(_test_ctx(php).test_command())[0] == "vendor/bin/phpunit"
    ruby = tmp_path / "ruby"
    _files(ruby, {"Gemfile": "", "Rakefile": ""})
    assert asyncio.run(_test_ctx(ruby).test_command())[0] == "bundle exec rake test"
    # A company that names only the stack gets that stack's runner.
    switched = asyncio.run(_test_ctx(ruby, {"stack": "go"}).test_command())
    assert switched[0] == "go test ./..."
    off = asyncio.run(_test_ctx(php, {"test": ""}).test_command())
    assert off == (None, "tests turned off by the company's stack profile")
    # npm's placeholder is no suite; another runner's suite still is.
    mixed = tmp_path / "mixed"
    _files(
        mixed,
        {
            "package.json": json.dumps(
                {"scripts": {"test": stack_profiles.NPM_DEFAULT_TEST}}
            ),
            "tests/test_a.py": "",
        },
    )
    assert asyncio.run(_test_ctx(mixed).test_command())[0] == "python -m pytest -q"
    bare = tmp_path / "bare"
    _files(bare, {"package.json": "{}"})
    assert asyncio.run(_test_ctx(bare).test_command()) == (
        None,
        "no test command found",
    )


def test_a_delivery_runs_the_profile_s_test_command(
    sqlite_session, tmp_path, monkeypatch
):
    company = _company(sqlite_session)
    monkeypatch.setattr(settings, "DELIVERY_INSTALL_ENABLED", False)
    _files(tmp_path, {"Gemfile": "", "Rakefile": ""})
    daemon = _Daemon()
    evidence = {"workspace_root": str(tmp_path)}
    asyncio.run(
        delivery_checks.run_delivery_tests(
            daemon,
            {"id": None, "company_id": company.id},
            evidence,
            sqlite_session.get_bind(),
        )
    )
    assert evidence["test_run"]["command"] == "bundle exec rake test"
    executed = [p["command"] for s, p in daemon.requests if s == "workspace.execute"]
    assert executed == ["bundle exec rake test"]


def test_the_integration_runs_the_tests_alone(sqlite_session, tmp_path, monkeypatch):
    company = _company(sqlite_session)
    monkeypatch.setattr(settings, "DELIVERY_APP_CHECK_ENABLED", True)
    monkeypatch.setattr(settings, "DELIVERY_TESTS_ENABLED", True)
    monkeypatch.setattr(
        settings, "DELIVERY_TEST_COMMAND", f"{PY} -c \"print('1 passed')\""
    )
    from services.release import integration

    daemon = _Daemon()
    test_run = asyncio.run(
        integration._run_suite(
            daemon,
            sqlite_session.get_bind(),
            {"sprint_id": None, "company_id": company.id},
            {"test_worktree": str(tmp_path), "commit": "abc"},
        )
    )
    assert test_run["exit_code"] == 0
    assert "workspace.app.check" not in daemon.subjects()


def test_the_reply_wait_covers_the_container_launch_and_teardown(monkeypatch):
    monkeypatch.setattr(settings, "DELIVERY_APP_CHECK_ENABLED", True)
    monkeypatch.setattr(settings, "DELIVERY_APP_READY_TIMEOUT_SECONDS", 60)
    waits = {}

    class _Nats:
        async def request(self, subject, payload, timeout=5.0):
            waits[subject] = timeout
            return {"started": True}

    ctx = project_profile.Context(_Nats(), {}, {"workspace_root": "/p"}, None)
    ctx._company = {"id": None, "overrides": {}, "installs": {}}
    ctx._profile = stack_profiles.resolve(
        {"stack": "node", "start": "npm start", "port": 3000}, {}
    )
    monkeypatch.setattr(
        "services.workspace.sandbox.request_for_sprint",
        lambda *_a: {"mode": "container"},
    )
    asyncio.run(delivery_checks.check_application(ctx))
    # docker run (120 s) and the runtime calls after the ready bound (4 x 30 s).
    assert (
        waits["workspace.app.check"]
        >= 60 + app_run.LAUNCH_SECONDS + 4 * app_run.RUNTIME_CALL_SECONDS
    )


def test_a_launch_that_times_out_still_removes_the_container(
    tmp_path, fake_docker, monkeypatch
):
    real_run = app_run.subprocess.run

    def slow_run(argv, *a, **kw):
        if argv[1:2] == ["run"]:
            raise app_run.subprocess.TimeoutExpired(argv, kw.get("timeout"))
        return real_run(argv, *a, **kw)

    monkeypatch.setattr(app_run.subprocess, "run", slow_run)
    reply = app_run.check_application(
        {
            "workspace_root": str(tmp_path),
            "start_command": SERVE,
            "port": _free_port(),
            "sandbox": {"mode": "container"},
        }
    )
    assert "did not start the container within" in reply["error"]
    assert "started" not in reply
    assert any(
        line.startswith("rm -f fabrique-app-")
        for line in fake_docker.read_text().splitlines()
    )


def test_a_log_that_cannot_be_read_still_removes_the_container(
    tmp_path, fake_docker, monkeypatch
):
    work = tmp_path / "work"
    work.mkdir()
    real = app_run._docker

    def failing_logs(runtime, *args, **kw):
        if args[0] == "logs":
            raise app_run.subprocess.TimeoutExpired([runtime, *args], 30)
        return real(runtime, *args, **kw)

    monkeypatch.setattr(app_run, "_docker", failing_logs)
    port = _free_port()
    reply = app_run.check_application(
        {
            "workspace_root": str(work),
            "start_command": SERVE,
            "port": port,
            "ready_timeout_seconds": 20,
            "sandbox": {"mode": "container"},
        }
    )
    assert reply["listening"] and reply["stopped"], reply
    assert "could not be read" in reply["log"]["output_tail"]
    assert any(
        line.startswith("rm -f ") for line in fake_docker.read_text().splitlines()
    )


def test_on_windows_the_whole_process_tree_is_killed(monkeypatch):
    import subprocess
    from types import SimpleNamespace

    import sandbox as daemon_sandbox

    calls = []
    monkeypatch.setattr(daemon_sandbox, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(
        daemon_sandbox,
        "subprocess",
        SimpleNamespace(run=lambda argv, **kw: calls.append(argv)),
    )

    class _Process:
        pid = 4242
        killed = False

        def poll(self):
            return 0

        def kill(self):
            self.killed = True

    daemon_sandbox.kill_tree(_Process())
    assert calls == [["taskkill", "/T", "/F", "/PID", "4242"]]
    monkeypatch.undo()

    # The application's stop asks for the tree, never terminate() alone.
    trees = []
    process = subprocess.Popen([PY, "-c", "import time; time.sleep(30)"])
    monkeypatch.setattr(app_run, "_WINDOWS", True)
    monkeypatch.setattr(
        app_run.sandbox, "kill_tree", lambda p: (trees.append(p.pid), p.kill())
    )
    monkeypatch.setattr(
        process, "terminate", lambda: (_ for _ in ()).throw(AssertionError("terminate"))
    )
    assert app_run._signal_leader(process)
    assert trees == [process.pid]


def _gone(pid: int) -> bool:
    import time

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        try:
            with open(f"/proc/{pid}/stat") as fh:
                if fh.read().split(")")[-1].split()[0] == "Z":
                    return True
        except OSError:
            return True
        time.sleep(0.05)
    return False


@pytest.mark.skipif(os.name == "nt", reason="process groups are POSIX")
def test_a_timed_out_e2e_command_leaves_no_child_running(tmp_path):
    import sandbox as daemon_sandbox

    pid_file = tmp_path / "child.pid"
    hung = daemon_sandbox.run_bounded(
        ["/bin/sh", "-c", f"sleep 60 & echo $! > {pid_file}; wait"], 1, own_group=True
    )
    assert hung["timed_out"]
    assert _gone(int(pid_file.read_text()))
    left = daemon_sandbox.run_bounded(
        ["/bin/sh", "-c", f"sleep 60 & echo $! > {pid_file}"], 10, own_group=True
    )
    assert left["exit_code"] == 0
    assert _gone(int(pid_file.read_text()))
