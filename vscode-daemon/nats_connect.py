"""
How the workspace daemon connects to NATS: credentials, TLS and the streams.

Anything that can publish on the bus can ask this daemon to write files and run
commands on this computer, so the broker gives the daemon its own user
(``daemon`` in ``nats/nats-server.conf``) that may answer requests but never
send one. The settings, read from the environment:

- ``NATS_CREDS_FILE``: a ``.creds`` file (JWT and nkey seed). Wins over the rest.
- ``NATS_NKEY_SEED``: the path of an nkey seed file.
- ``NATS_USER`` / ``NATS_PASSWORD``: a user and password.
- ``NATS_TLS=true``: connect with TLS; ``NATS_TLS_CA`` pins the CA that signed
  the server's certificate, ``NATS_TLS_CERT`` / ``NATS_TLS_KEY`` present a
  client certificate.

None set keeps the old behaviour (no authentication), with a warning when the
broker is not on this machine. No secret is ever logged.

The backend reads the same variables (``backend/app/nats_auth.py``); keep the
two in step. Streams are the backend's to create: the daemon's user may not, so
``wait_for_streams`` waits until they exist.
"""

import asyncio
import logging
import os
import platform
import ssl
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional
from urllib.parse import urlsplit

logger = logging.getLogger("fabrique-daemon")

# Replies to this daemon's own requests (JetStream calls) arrive here; the
# broker lets only the daemon subscribe under it. nats-py 2.3.1 ignores the
# connection's prefix for pull consumers, so the consumer passes it too.
INBOX_PREFIX = "_INBOX.daemon"
PULL_INBOX_PREFIX = (INBOX_PREFIX + ".").encode()
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "[::1]"})
_TRUE = frozenset({"1", "true", "yes", "on"})


class NATSConfigError(ValueError):
    """The NATS settings are incomplete or name a file that cannot be read."""


def _value(env: Mapping[str, str], name: str) -> str:
    return (env.get(name) or "").strip()


def _readable_file(env: Mapping[str, str], name: str) -> str:
    path = Path(_value(env, name)).expanduser()
    if not path.is_file():
        raise NATSConfigError(f"{name} names a file that does not exist: {_value(env, name)}")
    return str(path)


def auth_method(env: Optional[Mapping[str, str]] = None) -> str:
    """``creds``, ``nkey``, ``password`` or ``none``."""
    env = os.environ if env is None else env
    if _value(env, "NATS_CREDS_FILE"):
        return "creds"
    if _value(env, "NATS_NKEY_SEED"):
        return "nkey"
    if _value(env, "NATS_USER"):
        return "password"
    return "none"


def tls_enabled(env: Optional[Mapping[str, str]] = None) -> bool:
    env = os.environ if env is None else env
    return _value(env, "NATS_TLS").lower() in _TRUE


def tls_context(env: Optional[Mapping[str, str]] = None) -> Optional[ssl.SSLContext]:
    env = os.environ if env is None else env
    if not tls_enabled(env):
        return None
    cafile = _readable_file(env, "NATS_TLS_CA") if _value(env, "NATS_TLS_CA") else None
    context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cafile=cafile)
    cert, key = _value(env, "NATS_TLS_CERT"), _value(env, "NATS_TLS_KEY")
    if cert or key:
        if not (cert and key):
            raise NATSConfigError("NATS_TLS_CERT and NATS_TLS_KEY go together")
        context.load_cert_chain(_readable_file(env, "NATS_TLS_CERT"), _readable_file(env, "NATS_TLS_KEY"))
    return context


def connect_options(env: Optional[Mapping[str, str]] = None) -> Dict[str, Any]:
    """Keyword arguments for ``nats.connect`` (credentials, TLS, inbox prefix)."""
    env = os.environ if env is None else env
    options: Dict[str, Any] = {"inbox_prefix": INBOX_PREFIX}
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


def describe(env: Optional[Mapping[str, str]] = None) -> Dict[str, Any]:
    env = os.environ if env is None else env
    method = auth_method(env)
    return {"authenticated": method != "none", "auth": method, "tls": tls_enabled(env)}


def exposed_warning(url: str, env: Optional[Mapping[str, str]] = None) -> Optional[str]:
    """A warning when the daemon talks without credentials to a broker other machines may reach."""
    env = os.environ if env is None else env
    if auth_method(env) != "none":
        return None
    host = _value(env, "NATS_BIND") or (urlsplit(url).hostname or "127.0.0.1")
    if host in LOOPBACK_HOSTS:
        return None
    return (
        f"NATS at {host} without credentials: anything that reaches it can ask this "
        "daemon to run commands. Set NATS_USER/NATS_PASSWORD (or --creds FILE); "
        "see docs/OPERATIONS.md, Securing the bus."
    )


def safe_url(url: str) -> str:
    """*url* without a user or password, for logs."""
    parts = urlsplit(url)
    if not (parts.username or parts.password):
        return url
    host = parts.hostname or ""
    netloc = f"{host}:{parts.port}" if parts.port else host
    return parts._replace(netloc=netloc).geturl()


async def connect_with_retry(url: str):
    """Connect to NATS, waiting for it if the backend stack is not up yet."""
    import nats

    options = connect_options()
    warning = exposed_warning(url)
    if warning:
        logger.warning(warning)
    security = describe()
    delay = 1.0
    while True:
        try:
            nc = await nats.connect(
                url,
                name=f"fabrique-daemon@{platform.node()}",
                max_reconnect_attempts=-1,
                reconnect_time_wait=2,
                **options,
            )
            logger.info("Connected to NATS at %s (auth: %s, TLS: %s)", safe_url(url), security["auth"], "on" if security["tls"] else "off")
            return nc
        except Exception as e:  # noqa: BLE001 - Keep waiting for the broker.
            logger.warning("NATS at %s unavailable (%s); retrying in %.0fs", safe_url(url), e, delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30)


async def wait_for_streams(js, names: Iterable[str], interval: float = 5.0) -> None:
    """Return once every stream in *names* exists; the backend creates them."""
    pending = list(names)
    told = False
    while pending:
        try:
            await js.stream_info(pending[0])
            pending.pop(0)
            continue
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 - Not created yet, or JetStream not ready.
            if not told:
                logger.info("Waiting for the backend to create stream %s (%s)", pending[0], type(e).__name__)
                told = True
        await asyncio.sleep(interval)
