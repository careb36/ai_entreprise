"""A DNS resolver the SSRF tests control (``services/platform/egress.py``).

``FakeResolver`` answers like ``socket.getaddrinfo``: an IP literal, in any
form the C library accepts (``2130706433``, ``0177.0.0.1``, ``127.1``),
resolves to itself; ``localhost`` names to the loopback addresses; any other
name to what ``names`` says, a list of answers taken in turn (the last one
repeats), so a name can answer a public address first and a private one
afterwards, as a rebinding DNS server does. A name it does not know resolves
to ``default`` (a public address) or, with ``default=None``, not at all.
"""

from __future__ import annotations

import ipaddress
import socket

PUBLIC = "93.184.215.14"


def _literal(host: str) -> str | None:
    try:
        return str(ipaddress.ip_address(host.split("%", 1)[0]))
    except ValueError:
        pass
    try:
        return socket.inet_ntoa(socket.inet_aton(host))
    except OSError:
        return None


class FakeResolver:
    def __init__(self, names=None, default: str | None = PUBLIC):
        self.names = {k.lower(): list(v) for k, v in (names or {}).items()}
        self.default = default
        self.calls: list[str] = []

    def _answer(self, host: str) -> list[str]:
        literal = _literal(host)
        if literal is not None:
            return [literal]
        name = host.lower().rstrip(".")
        if name == "localhost" or name.endswith(".localhost"):
            return ["127.0.0.1", "::1"]
        answers = self.names.get(name)
        if answers:
            current = answers[0] if len(answers) == 1 else answers.pop(0)
            return current if isinstance(current, list) else [current]
        if self.default is None:
            raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")
        return [self.default]

    def __call__(self, host, port=None, *args, **kwargs):
        self.calls.append(host)
        out = []
        for address in self._answer(host):
            family = socket.AF_INET6 if ":" in address else socket.AF_INET
            out.append((family, socket.SOCK_STREAM, 6, "", (address, port or 0)))
        return out
