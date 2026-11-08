"""Command policy (services.command_policy)."""

from services import command_policy


def test_deny_list_blocks():
    ok, _ = command_policy.check_command("sudo rm -rf /", mode="deny", deny=["sudo"])
    assert ok is False


def test_allow_list_permits_listed():
    ok, _ = command_policy.check_command("npm test", mode="allow", allow=["npm"])
    assert ok is True


def test_allow_list_rejects_unlisted():
    ok, _ = command_policy.check_command("curl x", mode="allow", allow=["npm"])
    assert ok is False


def test_composite_requires_all_parts():
    ok, _ = command_policy.check_command(
        "npm test && rm -rf /", mode="deny", deny=["rm -rf /"]
    )
    assert ok is False


def test_empty_rejected():
    ok, _ = command_policy.check_command("", mode="deny")
    assert ok is False
