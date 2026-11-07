"""Sandbox mode as a verification check (services.sandbox_check)."""

from services import sandbox_check


def test_container_mode_passes():
    r = sandbox_check.check_sandbox(
        {"sandbox": {"mode": "container", "limits": {"cpu": 1}}}
    )
    assert r["outcome"] == "pass"
    assert r["mode"] == "container"


def test_host_mode_is_a_finding_not_a_refutation():
    r = sandbox_check.check_sandbox({"sandbox": {"mode": "host", "limits": {}}})
    assert r["outcome"] == "pass"
    assert r["finding"] == "host_mode"


def test_missing_sandbox_is_skipped():
    assert sandbox_check.check_sandbox({}) is None
    assert sandbox_check.check_sandbox(None) is None
