"""Where an outgoing request may go: public addresses only, resolved once.

The notification webhook, the OIDC issuer client and the research fetches
reach a URL that a company (an Owner, a Manager) chose. Without a guard they
turn the backend into a reader of its own network (SSRF, WSTG-v42-INPV-19,
which is WSTG-INJT-19 at latest@87cf4a4):
the cloud metadata service, a database on the LAN, the NATS monitor.

``pin`` resolves the URL's host once, refuses it unless every address it
resolves to is public (``address_is_public``), and returns a ``Target`` that
names the address to connect to. The HTTP adapter connects to that address
and sends the original host in ``Host`` and in TLS SNI, so certificate
verification is unchanged and a name that resolves to a public address for
the check and to a private one for the connection (DNS rebinding) cannot
redirect it. Each adapter refuses redirects, so a public host cannot bounce
the request inward either.

``address_is_public`` refuses everything ``ipaddress`` does not call global,
plus multicast, and looks inside IPv6 forms that carry an IPv4 address
(mapped ``::ffff:a.b.c.d``, NAT64 ``64:ff9b::/96``, 6to4, Teredo), which
Python's own check lets through for NAT64.

Pure: no HTTP library. ``RESOLVER`` is ``socket.getaddrinfo``, replaced in
the tests by a fake that answers what a hostile DNS server would.
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

RESOLVER: Any = socket.getaddrinfo
_NAT64 = ipaddress.ip_network("64:ff9b::/96")


class EgressRefused(ValueError):
    """The URL's host is not a public address (or does not resolve)."""


def address_is_public(address: str) -> bool:
    """Whether *address* (an IP literal) is a public unicast address."""
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return False
    if not ip.is_global or ip.is_multicast:
        return False
    if isinstance(ip, ipaddress.IPv6Address):
        embedded = [ip.ipv4_mapped, ip.sixtofour]
        if ip.teredo:
            embedded.extend(ip.teredo)
        if ip in _NAT64:
            embedded.append(ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF))
        for inner in embedded:
            if inner is not None and (not inner.is_global or inner.is_multicast):
                return False
    return True


def resolve(host: str, port: int | None = None) -> list[str]:
    """Every address *host* resolves to (an IP literal resolves to itself)."""
    try:
        infos = RESOLVER(host, port, proto=socket.IPPROTO_TCP)
    except (OSError, UnicodeError) as exc:
        raise EgressRefused(f"{host}: does not resolve") from exc
    addresses: list[str] = []
    for info in infos or []:
        address = str(info[4][0])
        if address not in addresses:
            addresses.append(address)
    if not addresses:
        raise EgressRefused(f"{host}: does not resolve")
    return addresses


def all_public(host: str) -> bool:
    """Whether *host* resolves, and only to public addresses."""
    try:
        return all(address_is_public(a) for a in resolve(host))
    except EgressRefused:
        return False


@dataclass(frozen=True)
class Target:
    """Where to send a request for ``url``.

    ``connect_url`` is ``url`` with its host replaced by the checked address
    (or ``url`` itself when nothing was pinned); ``host_header`` and
    ``sni_hostname`` carry the original host for the server and for TLS.
    """

    url: str
    connect_url: str
    host_header: str | None = None
    sni_hostname: str | None = None

    @classmethod
    def unpinned(cls, url: str) -> Target:
        return cls(url=url, connect_url=url)

    def headers(self) -> dict[str, str]:
        return {"Host": self.host_header} if self.host_header else {}

    def extensions(self) -> dict[str, str]:
        return {"sni_hostname": self.sni_hostname} if self.sni_hostname else {}


def pin(url: str, *, allow_private: bool = False) -> Target:
    """The ``Target`` for *url*: its host resolved once and checked.

    With *allow_private* (an operator's explicit choice: a Keycloak on the
    LAN, a local mirror) nothing is checked or pinned.
    """
    if allow_private:
        return Target.unpinned(url)
    parts = urlsplit(url)
    host = parts.hostname
    if parts.scheme not in {"http", "https"} or not host:
        raise EgressRefused("not an http(s) URL")
    default_port = 443 if parts.scheme == "https" else 80
    addresses = resolve(host, parts.port or default_port)
    refused = [a for a in addresses if not address_is_public(a)]
    if refused:
        raise EgressRefused(f"{host}: resolves to a private or reserved address")
    address = addresses[0]
    literal = f"[{address}]" if ":" in address else address
    netloc = literal if parts.port is None else f"{literal}:{parts.port}"
    host_header = parts.netloc.rsplit("@", 1)[-1]
    connect_url = urlunsplit(
        (parts.scheme, netloc, parts.path, parts.query, parts.fragment)
    )
    return Target(
        url=url,
        connect_url=connect_url,
        host_header=host_header,
        sni_hostname=host if parts.scheme == "https" else None,
    )
