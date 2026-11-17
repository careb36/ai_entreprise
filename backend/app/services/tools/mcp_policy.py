"""What an MCP server may be: the operator's allowlist and the public-address rule.

A stdio server is a process the backend spawns with its own privileges, so it
is off unless the operator names the command in ``MCP_STDIO_ALLOWED_COMMANDS``
(comma-separated absolute paths, set in the environment, never through the
API). ``check_stdio`` refuses a command that is not exactly one of those
paths (no relative path, no ``..``, no other spelling or case, no symlink
under another name), arguments that are not a short list of strings, an
environment variable that changes how a program loads its code
(``LD_PRELOAD``, ``PATH``, ``PYTHONPATH``, ``NODE_OPTIONS``...) and a working
directory that is not an absolute path. No shell is involved: the client
spawns ``[command, *args]``, so an argument is never interpreted.

An HTTP or SSE server's ``url`` must be https (http only for a host the
operator names in ``MCP_HTTP_ALLOWED_HOSTS``), carry no credentials, and its
host must resolve only to public addresses (``services/platform/egress.py``,
the helper the webhook, the OIDC client and the research fetches use). The
check runs when the server is saved (``check_url``) and again right before
each connection (``pin_url``), which returns the address to connect to, so a
DNS answer that changes in between (rebinding) cannot reach the backend's
own network.

Pure apart from the resolver: no HTTP library. Raises ``McpPolicyError``
with a reason meant for the person who configured the server.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Mapping
from typing import Any
from urllib.parse import urlsplit

from config import settings

from services.platform import egress

MAX_ARGS = 64
MAX_ARG_CHARS = 4096
MAX_URL_CHARS = 2000
STDIO_DISABLED = (
    "stdio MCP servers are disabled on this deployment: an operator enables a "
    "command by its absolute path in MCP_STDIO_ALLOWED_COMMANDS"
)
# Programs whose arguments choose what runs: allowing one allows any code.
_RUNNER = re.compile(
    r"^(npx|npm|pnpm|yarn|bunx?|uvx?|pipx|node|nodejs|deno|python[0-9.]*|pypy[0-9.]*"
    r"|ruby|perl|php|java|env|sudo|doas|docker|podman|busybox|(ba|da|z|k|c|tc|fi)?sh)$"
)
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
# Variables that make a program load or run other code than its own.
_ENV_DENIED_NAMES = frozenset(
    {
        "PATH",
        "BASH_ENV",
        "ENV",
        "IFS",
        "SHELLOPTS",
        "PS4",
        "GCONV_PATH",
        "LOCPATH",
        "NLSPATH",
        "HOSTALIASES",
        "RESOLV_HOST_CONF",
        "GLIBC_TUNABLES",
        "JAVA_TOOL_OPTIONS",
        "_JAVA_OPTIONS",
        "JDK_JAVA_OPTIONS",
        "CLASSPATH",
    }
)
_ENV_DENIED_PREFIXES = (
    "LD_",
    "DYLD_",
    "MALLOC_",
    "PYTHON",
    "NODE_",
    "NPM_CONFIG_",
    "YARN_",
    "BUN_",
    "DENO_",
    "PERL",
    "RUBY",
    "GEM_",
    "UV_",
    "PIP_",
    "GIT_",
)


class McpPolicyError(ValueError):
    """The server is not allowed on this deployment; the message says why."""


def _split(value: str | None) -> list[str]:
    return [item.strip() for item in (value or "").split(",") if item.strip()]


def allowed_commands() -> frozenset[str]:
    """The operator's allowlist: absolute, normalized paths only."""
    return frozenset(
        entry
        for entry in _split(settings.MCP_STDIO_ALLOWED_COMMANDS)
        if os.path.isabs(entry) and os.path.normpath(entry) == entry
    )


def http_allowed_hosts() -> frozenset[str]:
    return frozenset(
        h.lower().rstrip(".") for h in _split(settings.MCP_HTTP_ALLOWED_HOSTS)
    )


def stdio_enabled() -> bool:
    return bool(allowed_commands())


def startup_warnings() -> list[str]:
    """What the backend logs at startup about the operator's MCP settings."""
    entries = _split(settings.MCP_STDIO_ALLOWED_COMMANDS)
    allowed = allowed_commands()
    warnings = [
        f"MCP_STDIO_ALLOWED_COMMANDS: {entry!r} is not an absolute, normalized "
        "path and allows nothing"
        for entry in entries
        if entry not in allowed
    ]
    if allowed:
        warnings.append(
            "stdio MCP servers are enabled for: " + ", ".join(sorted(allowed))
        )
    runners = sorted(c for c in allowed if _RUNNER.match(os.path.basename(c)))
    if runners:
        warnings.append(
            "MCP_STDIO_ALLOWED_COMMANDS allows an interpreter, package runner or "
            f"shell ({', '.join(runners)}): a Manager can run any code as the "
            "backend through its arguments; allow a fixed server binary instead"
        )
    if http_allowed_hosts():
        warnings.append(
            "MCP servers may use plain http for: "
            + ", ".join(sorted(http_allowed_hosts()))
        )
    return warnings


def check_env_names(names: Iterable[str]) -> None:
    for name in names:
        upper = str(name).upper()
        if not _ENV_NAME.match(str(name)):
            raise McpPolicyError(f"env: {name!r} is not a variable name")
        if upper in _ENV_DENIED_NAMES or upper.startswith(_ENV_DENIED_PREFIXES):
            raise McpPolicyError(
                f"env: {name} changes how a program loads its code and is refused "
                "for a stdio server"
            )


def check_stdio(
    command: Any,
    args: Any = None,
    env_names: Iterable[str] = (),
    cwd: Any = None,
) -> str:
    """*command* if the operator allows it with these arguments, else ``McpPolicyError``."""
    allowed = allowed_commands()
    if not allowed:
        raise McpPolicyError(STDIO_DISABLED)
    if not isinstance(command, str) or not command or "\x00" in command:
        raise McpPolicyError("command: not a path")
    if not os.path.isabs(command) or os.path.normpath(command) != command:
        raise McpPolicyError(
            "command: must be an absolute, normalized path on MCP_STDIO_ALLOWED_COMMANDS"
        )
    if command not in allowed:
        raise McpPolicyError(
            f"command: {command} is not on this deployment's MCP_STDIO_ALLOWED_COMMANDS"
        )
    args = list(args or [])
    if len(args) > MAX_ARGS or not all(
        isinstance(a, str) and "\x00" not in a and len(a) <= MAX_ARG_CHARS for a in args
    ):
        raise McpPolicyError(
            f"args: at most {MAX_ARGS} strings of at most {MAX_ARG_CHARS} characters"
        )
    check_env_names(env_names)
    if cwd is not None and (
        not isinstance(cwd, str)
        or "\x00" in cwd
        or not os.path.isabs(cwd)
        or os.path.normpath(cwd) != cwd
    ):
        raise McpPolicyError("cwd: must be an absolute, normalized path")
    return command


def check_stdio_spec(spec: Mapping[str, Any]) -> None:
    """``check_stdio`` over a connection spec (``mcp_config.connection_spec``)."""
    check_stdio(
        spec.get("command"),
        spec.get("args"),
        (spec.get("env") or {}).keys(),
        spec.get("cwd"),
    )


def check_url(url: Any, *, resolve: bool = True) -> str:
    """*url* as an HTTP or SSE server's endpoint, else ``McpPolicyError``.

    With *resolve* the host must resolve, and only to public addresses.
    """
    if not isinstance(url, str):
        raise McpPolicyError("url: not a URL")
    url = url.strip()
    if not url or len(url) > MAX_URL_CHARS or any(ch.isspace() for ch in url):
        raise McpPolicyError("url: not a URL")
    try:
        # An unclosed or invalid IPv6 literal, or a port that is not a
        # number in range, raises a plain ValueError here or below.
        parts = urlsplit(url)
        host = (parts.hostname or "").lower().rstrip(".")
        parts.port  # noqa: B018 - raises on a bad port.
    except ValueError as exc:
        raise McpPolicyError("url: not a URL") from exc
    if not host:
        raise McpPolicyError("url: not a URL")
    if parts.scheme == "http":
        if host not in http_allowed_hosts():
            raise McpPolicyError(
                "url: must be https (an operator allows http for a host with "
                "MCP_HTTP_ALLOWED_HOSTS)"
            )
    elif parts.scheme != "https":
        raise McpPolicyError("url: must be an https URL")
    if parts.username or parts.password:
        raise McpPolicyError("url: credentials belong in the headers, not the URL")
    if resolve and not egress.all_public(host):
        raise McpPolicyError(
            "url: the host resolves to a private, loopback, link-local or "
            "reserved address, or does not resolve"
        )
    return url


def pin_url(url: Any) -> egress.Target:
    """Where to connect for *url*: checked again, resolved once and pinned."""
    url = check_url(url, resolve=False)
    try:
        return egress.pin(url)
    except egress.EgressRefused as exc:
        raise McpPolicyError(
            "url: the host resolves to a private, loopback, link-local or "
            "reserved address, or does not resolve"
        ) from exc


def check_spec(spec: Mapping[str, Any]) -> None:
    """The start check for a spec, without resolving: stdio allowlist or URL shape."""
    transport = spec.get("transport")
    if transport == "stdio":
        check_stdio_spec(spec)
    elif transport in ("http", "sse"):
        check_url(spec.get("url"), resolve=False)
