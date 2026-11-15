"""One set of SSRF vectors against every client that fetches a URL a company
chose (issue #541; WSTG-v42-INPV-19, which is WSTG-INJT-19 at latest@87cf4a4).

The clients: the outgoing notification webhook (``webhooks.check_url`` when
saved, ``webhooks._target`` before each post), the OIDC issuer client
(``oidc.check_url`` when configured, ``oidc_client`` on each fetch) and the
research fetches (``research_http.default_http``, through the toolbox). Each
must refuse a loopback, private, link-local, unspecified or reserved address
however it is spelled, a name that resolves to one, a redirect towards one,
and a name whose DNS answer changes between the check and the connection.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
from config import settings
from egress_fakes import PUBLIC, FakeResolver
from services.integrations import webhook_client, webhooks
from services.platform import egress, oidc, oidc_client
from services.tools import research_http, research_tools
from services.tools.research_registries import ResearchError

# Hosts as they appear in a URL, each an address no fetch may reach.
LITERAL_HOSTS = [
    "127.0.0.1",
    "127.1",
    "2130706433",  # decimal 127.0.0.1
    "0177.0.0.1",  # octal
    "0x7f.0.0.1",  # hex
    "017700000001",  # one octal number
    "[::1]",
    "0.0.0.0",
    "[::]",
    "169.254.169.254",  # cloud metadata
    "[::ffff:127.0.0.1]",  # IPv4-mapped IPv6
    "[::ffff:169.254.169.254]",
    "[64:ff9b::7f00:1]",  # NAT64 of 127.0.0.1, which ipaddress calls global
    "[2002:7f00:1::]",  # 6to4 of 127.0.0.1
    "[fe80::1]",
    "[fd00::1]",
    "10.0.0.5",
    "172.16.0.1",
    "192.168.1.1",
    "100.64.0.1",  # carrier-grade NAT
    "224.0.0.1",  # multicast, which ipaddress calls global
    "localhost",
    "LOCALHOST",
    "api.localhost",
]
# Names a hostile DNS server answers with a private address.
NAMES = {
    "metadata.attacker.test": ["169.254.169.254"],
    "lan.attacker.test": ["10.1.2.3"],
    "mixed.attacker.test": [[PUBLIC, "127.0.0.1"]],
    "v6.attacker.test": ["::1"],
}
VECTORS = LITERAL_HOSTS + list(NAMES)


@pytest.fixture
def dns(monkeypatch):
    resolver = FakeResolver(NAMES)
    monkeypatch.setattr(egress, "RESOLVER", resolver)
    return resolver


class _Recorder:
    """An httpx transport that records each request and answers *answer*."""

    def __init__(self, answer=None):
        self.requests: list[httpx.Request] = []
        self.answer = answer or (lambda request: httpx.Response(200, json={"ok": 1}))

    def handler(self, request):
        self.requests.append(request)
        return self.answer(request)

    def sync(self):
        return httpx.MockTransport(self.handler)


def _url(host, path="/x"):
    return f"https://{host}{path}"


# -- the address check itself ---------------------------------------------


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "::1",
        "0.0.0.0",
        "::",
        "169.254.169.254",
        "::ffff:127.0.0.1",
        "64:ff9b::a9fe:a9fe",
        "2002:a00:1::",
        "fe80::1%eth0",
        "224.0.0.1",
        "ff02::1",
        "100.64.0.1",
        "198.18.0.1",
        "192.0.2.1",
        "not an address",
    ],
)
def test_reserved_addresses_are_not_public(address):
    assert egress.address_is_public(address) is False


@pytest.mark.parametrize(
    "address", [PUBLIC, "8.8.8.8", "2606:4700:4700::1111", "::ffff:8.8.8.8"]
)
def test_public_addresses_are_public(address):
    assert egress.address_is_public(address) is True


# -- the outgoing webhook -------------------------------------------------


@pytest.mark.parametrize("host", VECTORS)
def test_the_webhook_refuses_the_vector_when_saved_and_before_each_post(
    dns, monkeypatch, host
):
    monkeypatch.delenv("WEBHOOK_ALLOW_PRIVATE_HOSTS", raising=False)
    with pytest.raises(webhooks.InvalidWebhook, match="private"):
        webhooks.check_url(_url(host))
    with pytest.raises(webhooks.InvalidWebhook, match="private"):
        webhooks._target(_url(host))


def test_the_webhook_posts_to_the_address_it_checked(dns, monkeypatch):
    """A name that answers a public address for the check and loopback for
    the connection (DNS rebinding) is posted to the checked address."""
    dns.names["rebind.attacker.test"] = [PUBLIC, "127.0.0.1"]
    recorder = _Recorder(lambda request: httpx.Response(204))
    monkeypatch.setattr(
        webhook_client, "TRANSPORT", httpx.MockTransport(recorder.handler)
    )
    target = webhooks._target("https://rebind.attacker.test/hook")
    status = asyncio.run(webhook_client.post(target, b"{}", {}, 5))
    [request] = recorder.requests
    assert status == 204
    assert request.url.host == PUBLIC
    assert request.headers["host"] == "rebind.attacker.test"
    assert request.extensions["sni_hostname"] == "rebind.attacker.test"
    assert dns.calls == ["rebind.attacker.test"]


def test_the_webhook_does_not_follow_a_redirect_inward(dns, monkeypatch):
    recorder = _Recorder(
        lambda request: httpx.Response(
            302, headers={"location": "https://169.254.169.254/"}
        )
    )
    monkeypatch.setattr(
        webhook_client, "TRANSPORT", httpx.MockTransport(recorder.handler)
    )
    target = webhooks._target("https://hooks.example.com/hook")
    assert asyncio.run(webhook_client.post(target, b"{}", {}, 5)) == 302
    assert len(recorder.requests) == 1


# -- the OIDC issuer client -----------------------------------------------


@pytest.fixture
def no_private_issuers(monkeypatch):
    monkeypatch.setattr(settings, "SSO_ALLOW_PRIVATE_ISSUERS", False)


@pytest.mark.parametrize("host", VECTORS)
def test_the_issuer_client_refuses_the_vector(dns, no_private_issuers, host):
    recorder = _Recorder()
    with pytest.raises(oidc.SsoError) as err:
        oidc.check_url(_url(host))
    assert err.value.code == "invalid_config"
    with pytest.raises(egress.EgressRefused):
        oidc_client.get_json(_url(host, "/jwks"), 5, transport=recorder.sync())
    with pytest.raises(egress.EgressRefused):
        oidc_client.post_form(_url(host, "/token"), {}, 5, transport=recorder.sync())
    assert recorder.requests == []


def test_a_cached_endpoint_that_resolves_inward_later_is_refused(
    dns, no_private_issuers, monkeypatch
):
    """The JWKS and token endpoints are fetched long after discovery checked
    them; each fetch checks again."""
    dns.names["idp.attacker.test"] = ["10.9.9.9"]
    monkeypatch.setattr(oidc, "HTTP_TRANSPORT", _Recorder().sync())
    with pytest.raises(oidc.SsoError) as err:
        oidc.jwks("https://idp.attacker.test/jwks", fresh=True)
    assert err.value.code == "invalid_config"


def test_the_issuer_client_fetches_the_address_it_checked(dns, no_private_issuers):
    dns.names["rebind.attacker.test"] = [PUBLIC, "127.0.0.1"]
    recorder = _Recorder()
    assert oidc_client.get_json(
        "https://rebind.attacker.test/jwks", 5, transport=recorder.sync()
    ) == {"ok": 1}
    [request] = recorder.requests
    assert request.url.host == PUBLIC
    assert request.headers["host"] == "rebind.attacker.test"
    assert dns.calls == ["rebind.attacker.test"]


def test_the_issuer_client_does_not_follow_a_redirect_inward(dns, no_private_issuers):
    recorder = _Recorder(
        lambda request: httpx.Response(302, headers={"location": "https://127.0.0.1/"})
    )
    with pytest.raises(oidc_client.OidcHttpError):
        oidc_client.get_json(
            "https://idp.example.com/jwks", 5, transport=recorder.sync()
        )
    assert len(recorder.requests) == 1


# -- the research fetches -------------------------------------------------


@pytest.fixture
def research_transport(monkeypatch):
    recorder = _Recorder(lambda request: httpx.Response(200, text="page"))
    monkeypatch.setattr(research_http, "TRANSPORT", recorder.sync())
    monkeypatch.setattr(settings, "RESEARCH_ALLOWED_HOSTS", "")
    return recorder


@pytest.mark.parametrize("host", VECTORS)
def test_a_research_fetch_refuses_the_vector(dns, research_transport, host):
    with pytest.raises(ResearchError, match="private"):
        research_http.default_http("GET", _url(host))
    with pytest.raises(ResearchError, match="private"):
        research_http.default_http("GET", f"http://{host}/x")
    assert research_transport.requests == []


@pytest.mark.parametrize(
    "host", ["169.254.169.254", "127.0.0.1", "localhost", "metadata.attacker.test"]
)
def test_a_host_on_a_companys_list_still_cannot_be_private(
    dns, research_transport, host
):
    """A Manager can add a host to the company's list, the IPv4 literal of a
    mirror included; that never opens the backend's own network."""
    box = research_tools.ResearchToolbox(hosts=research_tools.allowed_hosts([host]))
    with pytest.raises(ResearchError, match="private"):
        box._get(f"http://{host}/latest/meta-data", {})
    assert research_transport.requests == []


def test_a_redirect_to_an_allowed_name_that_resolves_inward_is_refused(
    dns, monkeypatch
):
    dns.names["docs.python.org"] = ["10.0.0.7"]
    monkeypatch.setattr(settings, "RESEARCH_ALLOWED_HOSTS", "")
    recorder = _Recorder(
        lambda request: (
            httpx.Response(302, headers={"location": "https://docs.python.org/3/"})
            if request.headers["host"] == "pypi.org"
            else httpx.Response(200, text="inner")
        )
    )
    monkeypatch.setattr(research_http, "TRANSPORT", recorder.sync())
    box = research_tools.ResearchToolbox()
    with pytest.raises(ResearchError, match="private"):
        box._get("https://pypi.org/project/x/", {})
    assert [r.headers["host"] for r in recorder.requests] == ["pypi.org"]


def test_a_research_fetch_goes_to_the_address_it_checked(dns, research_transport):
    dns.names["rebind.attacker.test"] = [PUBLIC, "127.0.0.1"]
    status, _headers, data = research_http.default_http(
        "GET", "https://rebind.attacker.test/x"
    )
    [request] = research_transport.requests
    assert (status, data) == (200, b"page")
    assert (
        request.url.host == PUBLIC and request.headers["host"] == "rebind.attacker.test"
    )


def test_a_host_the_deployment_named_may_be_private(
    dns, research_transport, monkeypatch
):
    """The operator's own mirror (the e2e's fake registry on 127.0.0.1)."""
    monkeypatch.setattr(settings, "RESEARCH_ALLOWED_HOSTS", "127.0.0.1")
    box = research_tools.ResearchToolbox()
    status, _headers, _data = box._get("http://127.0.0.1:5100/pypi/x/json", {})
    assert status == 200 and len(research_transport.requests) == 1
