"""The DNS adapter of the SSO domain verification (``docs/design/SSO.md``).

``sso_domains`` decides what an answer means; this module only asks a
resolver for the TXT records at a name and returns their strings (each
record's segments joined, decoded as UTF-8 with replacement, at most
``MAX_RECORDS`` of at most ``MAX_CHARS`` characters each). It never searches
the resolver's domain list, so the name asked is the name given.

Errors are named, never dnspython's: ``DnsNameNotFound`` is a definite
answer (NXDOMAIN, the name does not exist); ``DnsUnavailable`` is anything
that is not an answer (a timeout, no resolver answering, SERVFAIL, a
malformed reply), its message the exception's class name. A name that exists
without TXT records answers ``[]``.

The resolver is the system's unless *nameservers* (``SSO_DNS_NAMESERVERS``)
names IP addresses to ask instead. dnspython is used rather than the
standard library because ``socket`` resolves only addresses.

This is an adapter in the sense of ``http_adapters`` in
``docs/architecture/contexts.json`` (it returns data and raises named
errors, and the domain module calls it), but it is not listed there: the
checker (``scripts/check_architecture.py``) knows only HTTP libraries and
would report the entry as stale. The rest of the platform context does not
import ``dns``; ``sso_domains`` reaches DNS only through this module.
"""

from __future__ import annotations

import ipaddress

import dns.exception
import dns.resolver

MAX_RECORDS = 20
MAX_CHARS = 1024


class DnsNameNotFound(LookupError):
    """The name does not exist (NXDOMAIN): a definite answer."""


class DnsUnavailable(RuntimeError):
    """No answer could be had; the message names why (a class name)."""


def _resolver(nameservers: list[str] | None):
    if not nameservers:
        return dns.resolver.Resolver()
    addresses = []
    for entry in nameservers:
        try:
            addresses.append(str(ipaddress.ip_address(entry.strip())))
        except ValueError as exc:
            raise DnsUnavailable("InvalidNameserver") from exc
    resolver = dns.resolver.Resolver(configure=False)
    resolver.nameservers = addresses
    return resolver


def txt_records(
    name: str, *, timeout: float, nameservers: list[str] | None = None
) -> list[str]:
    """The TXT strings at *name*; ``DnsNameNotFound`` or ``DnsUnavailable``."""
    resolver = _resolver(nameservers)
    try:
        answer = resolver.resolve(name, "TXT", lifetime=timeout, search=False)
    except dns.resolver.NXDOMAIN as exc:
        raise DnsNameNotFound(name) from exc
    except dns.resolver.NoAnswer:
        return []
    except dns.exception.DNSException as exc:
        raise DnsUnavailable(type(exc).__name__) from exc
    values: list[str] = []
    for rdata in answer:
        if len(values) >= MAX_RECORDS:
            break
        joined = b"".join(rdata.strings)
        values.append(joined.decode("utf-8", errors="replace")[:MAX_CHARS])
    return values
