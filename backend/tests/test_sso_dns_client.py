"""The DNS adapter of the SSO domain verification (``services/platform/dns_client.py``):
it asks for TXT records and returns their strings, each record's segments
joined; "no such name" is ``DnsNameNotFound`` (a definite answer), anything
transient is ``DnsUnavailable`` naming the exception's class, never a
dnspython exception; the answer is bounded; named resolvers are used when the
operator sets them, and an entry that is not an IP address is refused. A fake
resolver stands in for dnspython's: no network."""

from __future__ import annotations

import dns.exception
import dns.resolver
import pytest
from services.platform import dns_client


class _Rdata:
    def __init__(self, *segments: bytes):
        self.strings = segments


class _FakeResolver:
    last: _FakeResolver | None = None
    next_answer: tuple | list | Exception = ()

    def __init__(self, configure=True):
        self.configure = configure
        self.nameservers: list[str] = []
        self.asked: list[tuple] = []
        _FakeResolver.last = self

    def resolve(self, name, rdtype, lifetime=None, search=None):
        self.asked.append((name, rdtype, lifetime, search))
        if isinstance(_FakeResolver.next_answer, Exception):
            raise _FakeResolver.next_answer
        return _FakeResolver.next_answer


@pytest.fixture
def resolver(monkeypatch):
    _FakeResolver.next_answer = []
    monkeypatch.setattr(dns.resolver, "Resolver", _FakeResolver)
    return _FakeResolver


def test_txt_strings_are_returned_with_their_segments_joined(resolver):
    resolver.next_answer = [_Rdata(b"la-fabrique-", b"token"), _Rdata(b"v=spf1 -all")]
    found = dns_client.txt_records("_x.acme.test", timeout=2.0)
    assert found == ["la-fabrique-token", "v=spf1 -all"]
    name, rdtype, lifetime, search = resolver.last.asked[0]
    assert (name, rdtype, lifetime, search) == ("_x.acme.test", "TXT", 2.0, False)
    assert resolver.last.configure is True


def test_no_such_name_is_a_definite_answer(resolver):
    resolver.next_answer = dns.resolver.NXDOMAIN()
    with pytest.raises(dns_client.DnsNameNotFound):
        dns_client.txt_records("_x.acme.test", timeout=2.0)


def test_a_name_without_txt_records_answers_nothing(resolver):
    resolver.next_answer = dns.resolver.NoAnswer()
    assert dns_client.txt_records("_x.acme.test", timeout=2.0) == []


@pytest.mark.parametrize(
    "error",
    [
        dns.exception.Timeout(),
        dns.resolver.NoNameservers(),
        dns.resolver.LifetimeTimeout(timeout=1.0, errors=[]),
        dns.exception.DNSException(),
    ],
)
def test_a_transient_failure_is_dns_unavailable(resolver, error):
    resolver.next_answer = error
    with pytest.raises(dns_client.DnsUnavailable) as raised:
        dns_client.txt_records("_x.acme.test", timeout=2.0)
    assert str(raised.value) == type(error).__name__


def test_the_answer_is_bounded(resolver):
    resolver.next_answer = [_Rdata(b"a" * 5000, b"\xff") for _ in range(50)]
    found = dns_client.txt_records("_x.acme.test", timeout=2.0)
    assert len(found) == dns_client.MAX_RECORDS
    assert all(len(value) == dns_client.MAX_CHARS for value in found)


def test_named_resolvers_replace_the_systems(resolver):
    dns_client.txt_records(
        "_x.acme.test", timeout=2.0, nameservers=["192.0.2.53", "2001:db8::53"]
    )
    assert resolver.last.configure is False
    assert resolver.last.nameservers == ["192.0.2.53", "2001:db8::53"]
    with pytest.raises(dns_client.DnsUnavailable):
        dns_client.txt_records("_x.acme.test", timeout=2.0, nameservers=["dns.test"])
