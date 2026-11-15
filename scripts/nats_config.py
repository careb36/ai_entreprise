#!/usr/bin/env python3
"""
Write the NATS server configuration: one user per component (issue #327).

Anything that can publish on the bus can ask the workspace daemon to write
files and run commands on the computer that holds the user's projects. So the
broker gives each component its own user, with only the subjects it needs:

- ``backend`` publishes the work (``task.>``, ``workspace.>``, ``glassroom.>``,
  ``audit.>``, ``system.>``) and owns JetStream: it creates the streams and
  consumes the task and result streams. Its replies arrive under
  ``_INBOX.backend``.
- ``daemon`` hears requests on ``workspace.>`` and answers them under
  ``_INBOX.backend.>``, where the backend's replies arrive; it may also
  publish its results (``workspace.delivered``), its heartbeat
  (``workspace.heartbeat``, which nobody answers), Glassroom status lines and
  the JetStream calls of its own consumer on the deliveries stream. It may
  not send a request (``workspace.execute``…), assign a task, read the
  backend's replies, or create, change or delete a stream.

  There is no ``allow_responses`` grant on purpose: it lets a client answer
  any reply subject it received, and the daemon hears ``workspace.>``. A
  holder of the daemon's credentials could publish to ``workspace.delivered``
  with ``workspace.execute`` as the reply subject, receive its own message,
  and "answer" it, which would send a command to the real daemon. Replies
  are allowed by subject instead, only under the backend's inbox.

The passwords are never in the file: it names ``$NATS_BACKEND_PASSWORD`` and
``$NATS_DAEMON_PASSWORD``, which nats-server reads from its environment
(``nats/start.sh`` refuses to start with only one of them set, and runs
without the file, unauthenticated as before, with neither).

    python3 scripts/nats_config.py             # write nats/nats-server.conf
    python3 scripts/nats_config.py --check     # fail when the committed file is stale
    python3 scripts/nats_config.py --namespace e2e --output /tmp/nats.conf

``--namespace`` mirrors ``NATS_NAMESPACE`` (``backend/app/nats_client.py``,
``vscode-daemon/nats_scope.py``): the durable subjects, stream and consumer
names carry the prefix. Design credit: OpenHands' scoped API keys and the
per-integration tokens of GitHub Copilot and Codex, one credential per
component with only what it needs.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = REPO / "nats" / "nats-server.conf"
BACKEND_INBOX = "_INBOX.backend"
DAEMON_INBOX = "_INBOX.daemon"
PASSWORD_VARS = {"backend": "NATS_BACKEND_PASSWORD", "daemon": "NATS_DAEMON_PASSWORD"}


def clean_namespace(raw: str) -> str:
    """The namespace as the backend and the daemon normalise it."""
    return re.sub(r"[^A-Za-z0-9_-]", "_", raw or "").strip("_")


def _name(namespace: str, name: str) -> str:
    return f"{namespace}_{name}" if namespace else name


def _subject(namespace: str, subject: str) -> str:
    return f"{namespace}.{subject}" if namespace else subject


def backend_permissions(namespace: str = "") -> dict:
    work = ["workspace.>", "task.>", "glassroom.>", "audit.>", "system.>"]
    durable = [_subject(namespace, s) for s in ("task.>", "workspace.>")] if namespace else []
    return {
        "publish": {"allow": [*work, *durable, "$JS.API.>", "$JS.ACK.>"]},
        "subscribe": {
            "allow": [f"{BACKEND_INBOX}.>", "glassroom.>", "workspace.>", "task.>", *durable]
        },
    }


def daemon_permissions(namespace: str = "") -> dict:
    stream = _name(namespace, "FABRIQUE_DELIVERIES")
    results = _name(namespace, "FABRIQUE_DELIVERY_RESULTS")
    consumer = _name(namespace, "workspace-daemon")
    deliver = _subject(namespace, "workspace.deliver")
    api = "$JS.API"
    return {
        "publish": {
            "allow": [
                # Its answers to the backend's requests (nats_auth.INBOX_PREFIX).
                f"{BACKEND_INBOX}.>",
                _subject(namespace, "workspace.delivered"),
                # Its heartbeat (vscode-daemon/daemon_status.py): an event, never a request.
                "workspace.heartbeat",
                "glassroom.*",
                f"{api}.INFO",
                f"{api}.STREAM.INFO.{stream}",
                f"{api}.STREAM.INFO.{results}",
                f"{api}.CONSUMER.INFO.{stream}.{consumer}",
                f"{api}.CONSUMER.CREATE.{stream}.{consumer}",
                f"{api}.CONSUMER.CREATE.{stream}.{consumer}.{deliver}",
                f"{api}.CONSUMER.DURABLE.CREATE.{stream}.{consumer}",
                f"{api}.CONSUMER.MSG.NEXT.{stream}.{consumer}",
                f"$JS.ACK.{stream}.{consumer}.>",
            ]
        },
        "subscribe": {"allow": ["workspace.>", f"{DAEMON_INBOX}.>"]},
    }


def build(namespace: str = "") -> dict:
    """The configuration as data: what ``render`` writes and ``parse`` reads back."""
    namespace = clean_namespace(namespace)
    users = []
    for user, permissions in (
        ("backend", backend_permissions(namespace)),
        ("daemon", daemon_permissions(namespace)),
    ):
        users.append(
            {"user": user, "password": f"${PASSWORD_VARS[user]}", "permissions": permissions}
        )
    # JetStream and its store are command-line flags (nats/start.sh: -js -sd).
    return {"authorization": {"users": users}}


def render(config: dict, namespace: str = "") -> str:
    """NATS configuration text: JSON values, with the passwords as variable references."""
    header = [
        "# Generated by scripts/nats_config.py; do not edit by hand.",
        "# One user per component; the passwords come from the environment",
        "# (NATS_BACKEND_PASSWORD, NATS_DAEMON_PASSWORD). See SECURITY.md.",
    ]
    if namespace:
        header.append(f"# Namespace: {clean_namespace(namespace)}")
    body = []
    for key, value in config.items():
        text = json.dumps(value, indent=2)
        for var in PASSWORD_VARS.values():
            # A quoted "$VAR" is a literal string; unquoted, nats-server reads the variable.
            text = text.replace(f'"${var}"', f"${var}")
        body.append(f"{key}: {text}")
    return "\n".join(header) + "\n\n" + "\n\n".join(body) + "\n"


def parse(text: str) -> dict:
    """Read back what ``render`` wrote (comments, ``key: value`` blocks, variable references)."""
    lines = [line for line in text.splitlines() if not line.lstrip().startswith("#")]
    source = "\n".join(lines)
    for var in PASSWORD_VARS.values():
        source = re.sub(rf"(?<![\"\w])\${var}\b", f'"${var}"', source)
    blocks = re.split(r"\n(?=[A-Za-z_]+: )", source.strip())
    entries = []
    for block in blocks:
        key, _, value = block.partition(": ")
        entries.append(f"{json.dumps(key)}: {value}")
    return json.loads("{" + ", ".join(entries) + "}")


def matches(pattern: str, subject: str) -> bool:
    """NATS subject matching: ``*`` is one token, ``>`` the rest (one or more)."""
    want, have = pattern.split("."), subject.split(".")
    for i, token in enumerate(want):
        if token == ">":
            return len(have) > i
        if i >= len(have) or (token != "*" and token != have[i]):
            return False
    return len(want) == len(have)


def allowed(permissions: dict, action: str, subject: str) -> bool:
    """Whether *permissions* let a client ``publish`` or ``subscribe`` on *subject*."""
    rules = permissions.get(action, {})
    if any(matches(p, subject) for p in rules.get("deny", [])):
        return False
    return any(matches(p, subject) for p in rules.get("allow", []))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--check", action="store_true", help="fail when the file is stale")
    parser.add_argument("--namespace", default="", help="NATS_NAMESPACE of the deployment")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    text = render(build(args.namespace), args.namespace)
    if args.check:
        current = args.output.read_text() if args.output.is_file() else ""
        if current != text:
            print(
                f"{args.output} is stale: run python3 scripts/nats_config.py"
                + (f" --namespace {args.namespace}" if args.namespace else ""),
                file=sys.stderr,
            )
            return 1
        print(f"{args.output} is up to date")
        return 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(text)
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
