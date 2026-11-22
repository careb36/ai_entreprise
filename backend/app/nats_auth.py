"""
How the backend authenticates to NATS and protects the connection (issue #327).

Anything that can publish on the bus can ask the workspace daemon to write
files and run commands on the computer that holds the user's projects, so the
broker gives each component its own user with only the subjects it needs
(``scripts/nats_config.py`` writes ``nats/nats-server.conf``). This module
turns the environment into ``nats.connect`` keyword arguments:

- ``NATS_CREDS_FILE``: a ``.creds`` file (JWT and nkey seed). Wins over the rest.
- ``NATS_NKEY_SEED``: the path of an nkey seed file.
- ``NATS_USER`` / ``NATS_PASSWORD``: a user and password.
- ``NATS_TLS=true``: connect with TLS; ``NATS_TLS_CA`` pins the CA that signed
  the server's certificate, ``NATS_TLS_CERT`` / ``NATS_TLS_KEY`` present a
  client certificate.

None set keeps today's behaviour: no authentication, which is safe only while
the broker listens on loopback (``NATS_BIND``); ``exposed_bind_warning`` says
so at startup. Nothing here ever logs or returns a secret: ``describe`` names
the method, never the value.

The workspace daemon reads the same variables (``vscode-daemon/nats_connect.py``);
keep the two in step.
"""

from __future__ import annotations

import os
import ssl
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

# Replies to this component's requests arrive under this prefix; the broker
# lets only the backend subscribe there.
INBOX_PREFIX = "_INBOX.backend"
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "[::1]"})
_TRUE = frozenset({"1", "true", "yes", "on"})


class NATSConfigError(ValueError):
    """The NATS settings are incomplete or name a file that cannot be read."""


def _value(env: Mapping[str, str], name: str) -> str:
    return (env.get(name) or "").strip()


def _readable_file(env: Mapping[str, str], name: str) -> str:
    path = _value(env, name)
    if not Path(path).expanduser().is_file():
        raise NATSConfigError(f"{name} names a file that does not exist: {path}")
    return str(Path(path).expanduser())


def auth_method(env: Mapping[str, str] | None = None) -> str:
    """``creds``, ``nkey``, ``password`` or ``none``: which credential is used."""
    env = os.environ if env is None else env
    if _value(env, "NATS_CREDS_FILE"):
        return "creds"
    if _value(env, "NATS_NKEY_SEED"):
        return "nkey"
    if _value(env, "NATS_USER"):
        return "password"
    return "none"


def tls_enabled(env: Mapping[str, str] | None = None) -> bool:
    env = os.environ if env is None else env
    return _value(env, "NATS_TLS").lower() in _TRUE


def tls_context(env: Mapping[str, str] | None = None) -> ssl.SSLContext | None:
    """The TLS context ``NATS_TLS`` asks for, or ``None`` for a plain connection."""
    env = os.environ if env is None else env
    if not tls_enabled(env):
        return None
    cafile = _readable_file(env, "NATS_TLS_CA") if _value(env, "NATS_TLS_CA") else None
    context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cafile=cafile)
    cert, key = _value(env, "NATS_TLS_CERT"), _value(env, "NATS_TLS_KEY")
    if cert or key:
        if not (cert and key):
            raise NATSConfigError("NATS_TLS_CERT and NATS_TLS_KEY go together")
        context.load_cert_chain(
            _readable_file(env, "NATS_TLS_CERT"), _readable_file(env, "NATS_TLS_KEY")
        )
    return context


def connect_options(env: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Keyword arguments for ``nats.connect`` from the environment."""
    env = os.environ if env is None else env
    options: dict[str, Any] = {"inbox_prefix": INBOX_PREFIX}
    method = auth_method(env)
    if method == "creds":
        options["user_credentials"] = _readable_file(env, "NATS_CREDS_FILE")
    elif method == "nkey":
        options["nkeys_seed"] = _readable_file(env, "NATS_NKEY_SEED")
    elif method == "password":
        password = env.get("NATS_PASSWORD") or ""
        if not password:
            raise NATSConfigError("NATS_USER is set without NATS_PASSWORD")
        options["user"] = _value(env, "NATS_USER")
        options["password"] = password
    context = tls_context(env)
    if context is not None:
        options["tls"] = context
    return options


def describe(env: Mapping[str, str] | None = None) -> dict[str, Any]:
    """What the connection uses, without any secret: for logs and ``/health``."""
    env = os.environ if env is None else env
    method = auth_method(env)
    return {"authenticated": method != "none", "auth": method, "tls": tls_enabled(env)}


def exposed_bind_warning(env: Mapping[str, str] | None = None) -> str | None:
    """A warning when the broker may be reachable from other machines without credentials."""
    env = os.environ if env is None else env
    bind = _value(env, "NATS_BIND") or "127.0.0.1"
    if auth_method(env) != "none" or bind in LOOPBACK_HOSTS:
        return None
    return (
        f"NATS_BIND is {bind} and NATS has no credentials: anything that reaches "
        "the broker can ask the workspace daemon to run commands. Set "
        "NATS_BACKEND_PASSWORD and NATS_DAEMON_PASSWORD (or NATS_CREDS_FILE), "
        "or bind to 127.0.0.1. See docs/OPERATIONS.md, Securing the bus."
    )


def safe_url(url: str) -> str:
    """*url* without a user or password, for logs."""
    parts = urlsplit(url)
    if not (parts.username or parts.password):
        return url
    host = parts.hostname or ""
    netloc = f"{host}:{parts.port}" if parts.port else host
    return parts._replace(netloc=netloc).geturl()
