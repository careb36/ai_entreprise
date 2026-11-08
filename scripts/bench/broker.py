#!/usr/bin/env python3
"""Refuse a NATS broker the bench must not share.

    python scripts/bench/broker.py check nats://127.0.0.1:54222

The daemon's live request subjects (``workspace.generate``,
``workspace.execute``, ...) are not namespaced, and every daemon joins the
same queue group (``workspace-daemon``). On a broker another stack uses, the
bench's deliveries and test runs could be answered by that stack's daemon,
on another machine's folders. So the bench runs only on a broker of its own:

- the default port (4222, the one ``docker compose`` publishes) is refused;
- a broker where a daemon already answers ``workspace.status`` is refused.

Exit status: 0 when the broker is the bench's alone, 1 when it is refused
(the reason on stderr), 2 when it cannot be reached. Run it before the
bench's own daemon starts. ``scripts/bench/run.sh`` avoids the question by
starting its own ``nats-server`` when one is installed.
"""

import asyncio
import sys
from urllib.parse import urlparse

DEFAULT_PORT = 4222
STATUS_SUBJECT = "workspace.status"


def default_port_reason(url: str) -> str | None:
    """Why *url* is refused for its port, or ``None``."""
    parsed = urlparse(url if "://" in url else f"nats://{url}")
    port = parsed.port or DEFAULT_PORT
    if port == DEFAULT_PORT:
        return (
            f"{url} is the default NATS port ({DEFAULT_PORT}), the one a local stack uses: "
            "the bench needs a broker of its own (BENCH_NATS_URL on another port, "
            "or nats-server installed so the bench starts one)."
        )
    return None


async def daemon_answers(url: str, timeout: float = 2.0, connect=None) -> bool:
    """True when something on *url* answers the daemon's status request.
    Raises ``OSError`` when the broker cannot be reached."""
    if connect is None:
        import nats  # the backend's environment has nats-py

        connect = nats.connect
    try:
        nc = await connect(
            url,
            connect_timeout=timeout,
            max_reconnect_attempts=0,
            allow_reconnect=False,
        )
    except Exception as exc:
        raise OSError(f"cannot reach {url}: {exc}") from exc
    try:
        await nc.request(STATUS_SUBJECT, b"{}", timeout=timeout)
        return True
    except Exception:  # noqa: BLE001 - a timeout or "no responders": nobody listens.
        return False
    finally:
        await nc.close()


def check(url: str, connect=None) -> tuple[int, str]:
    reason = default_port_reason(url)
    if reason:
        return 1, reason
    try:
        busy = asyncio.run(daemon_answers(url, connect=connect))
    except OSError as exc:
        return 2, str(exc)
    if busy:
        return 1, (
            f"a workspace daemon already answers on {url}: another stack uses this broker, "
            "and its daemon could take the bench's work. Give the bench a broker of its own."
        )
    return 0, f"{url} is free for the bench"


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2 or args[0] != "check":
        print("usage: broker.py check <nats url>", file=sys.stderr)
        return 2
    status, message = check(args[1])
    print(message, file=sys.stderr if status else sys.stdout)
    return status


if __name__ == "__main__":
    sys.exit(main())
