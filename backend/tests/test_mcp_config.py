"""The ``mcpServers`` map a company gives, and what the API gives back."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

import crypto
from config import settings
from egress_fakes import FakeResolver
from models.mcp import McpServer
from services.platform import egress
from services.tools import mcp_config

GITHUB_SERVER = "/opt/mcp/bin/server-github"


@pytest.fixture(autouse=True)
def operator(monkeypatch):
    """A deployment that allows the one stdio command named here; DNS answers public."""
    monkeypatch.setattr(egress, "RESOLVER", FakeResolver())
    monkeypatch.setattr(settings, "MCP_STDIO_ALLOWED_COMMANDS", GITHUB_SERVER)


@pytest.fixture()
def with_key(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(crypto, "_configured_key", lambda: key)
    return key


def test_a_standard_map_is_read_stdio_by_command_http_by_url():
    parsed = mcp_config.parse_mcp_servers(
        {
            "mcpServers": {
                "github": {
                    "command": GITHUB_SERVER,
                    "args": ["--toolsets", "@model-repos"],
                    "env": {"GITHUB_TOKEN": "t"},
                },
                "docs": {
                    "url": "https://docs.example/mcp",
                    "headers": {"Authorization": "Bearer x"},
                    "timeout": 30,
                },
                "legacy": {
                    "type": "sse",
                    "url": "https://old.example/sse",
                    "roles": ["QA"],
                    "enabled": False,
                    "ignored": 1,
                },
                "shttp": {
                    "transport": "streamable-http",
                    "url": "https://s.example/mcp",
                },
            }
        }
    )
    assert parsed["github"]["transport"] == "stdio" and parsed["github"]["args"][
        1
    ].startswith("@model")
    assert (
        parsed["github"]["env"] == {"GITHUB_TOKEN": "t"}
        and parsed["github"]["url"] is None
    )
    assert (
        parsed["docs"]["transport"] == "http"
        and parsed["docs"]["timeout_seconds"] == 30.0
    )
    assert parsed["docs"]["headers"] == {"Authorization": "Bearer x"}
    assert parsed["legacy"]["transport"] == "sse" and parsed["legacy"]["roles"] == [
        "QA"
    ]
    assert parsed["legacy"]["enabled"] is False
    assert parsed["shttp"]["transport"] == "http"
    # A bare map, without the mcpServers key, is the same thing.
    assert (
        mcp_config.parse_mcp_servers({"a": {"url": "https://a"}})["a"]["transport"]
        == "http"
    )


@pytest.mark.parametrize(
    ("servers", "message"),
    [
        ({"x": {"transport": "stdio"}}, "needs 'command'"),
        ({"x": {"transport": "http"}}, "needs 'url'"),
        ({"x": {"url": "https://a", "transport": "grpc"}}, "unknown transport"),
        ({"x": {"command": "npx", "args": "not a list"}}, "'args' must be a list"),
        (
            {"x": {"url": "https://a", "env": {"A": 1}}},
            "'env' must map names to strings",
        ),
        ({"x": {"url": "https://a", "timeout": -1}}, "'timeout' must be a positive"),
        ({"a__b": {"url": "https://a"}}, "without '__'"),
        ({"x": "https://a"}, "a server is an object"),
        ({}, "no servers given"),
        ([], "expected an object"),
    ],
)
def test_a_definition_the_backend_refuses_says_why(servers, message):
    with pytest.raises(mcp_config.McpConfigError, match=message):
        mcp_config.parse_mcp_servers(servers)


# A dot never occurs in Fernet's URL-safe base64, so the value cannot turn up
# in the ciphertext by chance (a three-letter value did, about once in 2000 runs).
HEADER_VALUE = "not.a.real.value"


def test_secrets_are_encrypted_at_rest_and_listed_by_name_only(with_key):
    fields = mcp_config.normalize_server(
        "docs",
        {
            "url": "https://docs.example/mcp",
            "headers": {"Authorization": "Bearer " + HEADER_VALUE},
            "env": {"K": "v"},
        },
    )
    row = mcp_config.apply(
        McpServer(company_id=1, name="docs", transport="http"), fields
    )
    assert row.secrets.startswith("fernet:") and HEADER_VALUE not in row.secrets
    shown = mcp_config.serialize(row)
    assert shown["header_names"] == ["Authorization"] and shown["env_keys"] == ["K"]
    assert (
        HEADER_VALUE not in str(shown)
        and "secrets" not in shown
        and shown["secrets_readable"] is True
    )
    spec = mcp_config.connection_spec(row)
    assert spec["headers"] == {"Authorization": "Bearer " + HEADER_VALUE} and spec[
        "env"
    ] == {"K": "v"}


def test_a_server_without_secrets_stores_none_and_an_unreadable_one_still_lists(
    with_key, monkeypatch
):
    row = mcp_config.apply(
        McpServer(company_id=1, name="plain", transport="http"),
        mcp_config.normalize_server("plain", {"url": "https://p"}),
    )
    assert row.secrets is None
    assert mcp_config.serialize(row)["env_keys"] == []
    row.secrets = crypto.encrypt_value('{"env": {"A": "b"}, "headers": {}}')
    monkeypatch.setattr(
        crypto, "_configured_key", lambda: Fernet.generate_key().decode()
    )
    shown = mcp_config.serialize(row)
    assert shown["secrets_readable"] is False and shown["env_keys"] == []


def test_the_operator_s_mcp_settings_are_named_at_startup(monkeypatch):
    from services.tools import mcp_policy

    monkeypatch.setattr(settings, "MCP_STDIO_ALLOWED_COMMANDS", "")
    monkeypatch.setattr(settings, "MCP_HTTP_ALLOWED_HOSTS", "")
    assert mcp_policy.startup_warnings() == []
    monkeypatch.setattr(
        settings,
        "MCP_STDIO_ALLOWED_COMMANDS",
        f"npx, /usr/bin/python3.11, {GITHUB_SERVER}, /bin/bash",
    )
    monkeypatch.setattr(settings, "MCP_HTTP_ALLOWED_HOSTS", "legacy.example")
    warnings = "\n".join(mcp_policy.startup_warnings())
    assert "'npx' is not an absolute" in warnings
    assert "/bin/bash, /opt/mcp/bin/server-github, /usr/bin/python3.11" in warnings
    assert "shell (/bin/bash, /usr/bin/python3.11)" in warnings
    assert GITHUB_SERVER not in warnings.split("shell (")[1]
    assert "plain http for: legacy.example" in warnings
