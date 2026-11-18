"""A fake DNS for the SSO domain verification tests: no network.

``FakeDns.install`` replaces ``dns_client.txt_records`` (the resolver
adapter, ``services/platform/dns_client.py``) with a lookup in ``answers``:
a name maps to the TXT strings it holds, or to an exception the adapter
would raise (``DnsNameNotFound``, ``DnsUnavailable``). A name with no entry
does not exist. Every name asked is kept in ``asked``.
"""

from __future__ import annotations

from services.platform import dns_client, sso_domains


class FakeDns:
    def __init__(self):
        self.answers: dict[str, list[str] | Exception] = {}
        self.asked: list[str] = []

    def lookup(self, name: str, **_kwargs) -> list[str]:
        self.asked.append(name)
        answer = self.answers.get(name)
        if answer is None:
            raise dns_client.DnsNameNotFound(name)
        if isinstance(answer, Exception):
            raise answer
        return list(answer)

    def publish(self, row, value: str | None = None) -> None:
        """Publish *row*'s expected value (or *value*) where it is looked up."""
        name = sso_domains.record_name(row.domain)
        self.answers[name] = [
            value if value is not None else sso_domains.record_value(row)
        ]

    def withdraw(self, row) -> None:
        self.answers.pop(sso_domains.record_name(row.domain), None)

    def install(self, monkeypatch) -> FakeDns:
        monkeypatch.setattr(dns_client, "txt_records", self.lookup)
        return self
