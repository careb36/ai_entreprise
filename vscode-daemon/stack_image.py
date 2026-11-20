"""The sandbox image built from the project's stacks (issue #766).

A single image for every project (``python:3.12-slim``, the old default)
could not run this repository's own tests: it needs Python with Poetry,
Node with npm, and git. Here the daemon builds the image the project needs:

1. ``toolchains`` reads which stacks the folder holds, by their marker
   files at the root and in its direct folders (``pyproject.toml``,
   ``package.json``, ``go.mod``…), from a fixed list.
2. ``dockerfile`` writes the Dockerfile for those toolchains from fixed
   templates: a Python base with git, Node and Go copied from their official
   images, Poetry and pytest pinned. Nothing in it comes from the project's
   files but the list of toolchain names, so an agent that writes in the
   worktree can add a toolchain at most, never a ``RUN`` line.
3. ``ensure`` tags it ``fabrique-stack:<first 16 hex of its sha256>``, uses
   the image already on the machine under that tag, else builds it with no
   build context (the Dockerfile on stdin: nothing of the host is sent to the
   build). One build per tag at a time; the report says whether it was built
   now, how long it took, the Dockerfile's sha256 and the toolchains.

A project's own ``.fabrique/sandbox.Dockerfile`` is not read: the worktree is
written by agents, and building it would run their ``RUN`` lines with the
network on. Reading it from the default branch's head, as the company
standards are, is ``docs/design/target/02-sandbox.md``'s later step.

The image's content ID is still observed before each run
(``sandbox_image.pinned``), so what ran is the daemon's observation, not this
module's tag.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import subprocess
import threading
import time
from typing import Any

BASE = "python:3.12-slim-bookworm"
NODE_IMAGE = "node:22-bookworm-slim"
GO_IMAGE = "golang:1.23-bookworm"
POETRY = "poetry==2.1.3"
PYTEST = "pytest==8.3.5"
TAG_PREFIX = "fabrique-stack"
LABEL = "dev.fabrique.stack-image"
# The longest one build may take (``FABRIQUE_STACK_BUILD_SECONDS``).
DEFAULT_BUILD_SECONDS = 1800
INSPECT_SECONDS = 30
SKIP_DIRS = frozenset({"node_modules", "venv", ".venv", "__pycache__", "dist", "build", "target", "vendor"})

MARKERS: dict[str, tuple[str, ...]] = {
    "python": ("pyproject.toml", "requirements.txt", "setup.py", "setup.cfg", "Pipfile"),
    "node": ("package.json",),
    "go": ("go.mod",),
}
ORDER = ("python", "node", "go")

_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _has_marker(folder: str, toolchain: str) -> bool:
    return any(os.path.isfile(os.path.join(folder, name)) for name in MARKERS[toolchain])


def toolchains(root: str) -> list[str]:
    """The toolchains the folder needs: markers at its root or in a direct folder."""
    folders = [root]
    try:
        with os.scandir(root) as entries:
            for entry in entries:
                if entry.is_dir(follow_symlinks=False) and not entry.name.startswith(".") and entry.name not in SKIP_DIRS:
                    folders.append(entry.path)
    except OSError:
        pass
    found = {name for name in ORDER for folder in folders if _has_marker(folder, name)}
    return [name for name in ORDER if name in found]


def dockerfile(names: list[str]) -> str:
    """The Dockerfile for *names*, from fixed templates only."""
    chosen = [name for name in ORDER if name in set(names)]
    lines = [
        f"FROM {BASE}",
        "RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates "
        "&& rm -rf /var/lib/apt/lists/*",
    ]
    if "python" in chosen:
        lines.append(f"RUN pip install --no-cache-dir {POETRY} {PYTEST}")
    if "node" in chosen:
        lines += [
            f"COPY --from={NODE_IMAGE} /usr/local/bin/node /usr/local/bin/node",
            f"COPY --from={NODE_IMAGE} /usr/local/lib/node_modules /usr/local/lib/node_modules",
            "RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm "
            "&& ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx",
        ]
    if "go" in chosen:
        lines += [
            f"COPY --from={GO_IMAGE} /usr/local/go /usr/local/go",
            "RUN ln -s /usr/local/go/bin/go /usr/local/bin/go && ln -s /usr/local/go/bin/gofmt /usr/local/bin/gofmt",
        ]
    lines.append(f'LABEL {LABEL}="{",".join(chosen) or "base"}"')
    return "\n".join(lines) + "\n"


def tag_for(text: str) -> tuple[str, str]:
    """The tag and the Dockerfile's sha256."""
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return f"{TAG_PREFIX}:{digest[:16]}", digest


def build_seconds() -> int:
    try:
        return max(60, int(os.getenv("FABRIQUE_STACK_BUILD_SECONDS", DEFAULT_BUILD_SECONDS)))
    except ValueError:
        return DEFAULT_BUILD_SECONDS


def _present(runtime: str, tag: str) -> bool:
    try:
        done = subprocess.run(
            [runtime, "image", "inspect", tag], capture_output=True, timeout=INSPECT_SECONDS, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0


def _lock(tag: str) -> threading.Lock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(tag, threading.Lock())


def ensure(runtime: str, root: str) -> dict[str, Any]:
    """The image for *root*'s stacks: on the machine already, or built now.

    The reply has ``image`` (the tag) on success, else ``error`` with the
    reason; never an image that was not built from the templates."""
    names = toolchains(root)
    text = dockerfile(names)
    tag, digest = tag_for(text)
    report: dict[str, Any] = {
        "image": None,
        "toolchains": names,
        "dockerfile_sha256": digest,
        "source": "generated",
        "observed_by": "daemon",
    }
    with _lock(tag):
        if _present(runtime, tag):
            return {**report, "image": tag, "built": False}
        started = time.monotonic()
        try:
            done = subprocess.run(
                [runtime, "build", "-t", tag, "-"],
                input=text.encode("utf-8"), capture_output=True, timeout=build_seconds(), check=False,
            )
        except subprocess.TimeoutExpired:
            return {**report, "error": f"the stack image build took longer than {build_seconds()}s"}
        except OSError as exc:
            return {**report, "error": f"{os.path.basename(runtime)} build could not start: {exc}"}
        took = int((time.monotonic() - started) * 1000)
        if done.returncode != 0:
            said = (done.stderr or b"").decode("utf-8", errors="replace").strip()[-800:]
            return {**report, "build_ms": took, "error": f"the stack image build failed (exit {done.returncode}): {said or 'no message'}"}
        return {**report, "image": tag, "built": True, "build_ms": took}


async def handle_image(nc, msg, reply, request_body, resolve) -> None:
    """``workspace.sandbox.image`` → the project's stack image, built when missing."""
    import sandbox

    root = request_body(msg).get("workspace_root")
    if not isinstance(root, str) or not os.path.isdir(root):
        body: dict[str, Any] = {"error": {"status": 400, "message": "workspace_root must be a folder on the daemon's machine"}}
    else:
        try:
            runtime = sandbox.container_runtime()
        except sandbox.SandboxUnavailable as exc:
            body = {"image": None, "toolchains": toolchains(root), "error": str(exc)}
        else:
            body = await asyncio.to_thread(ensure, runtime, root)
    await reply(nc, msg, body)


HANDLERS = (("workspace.sandbox.image", handle_image),)
