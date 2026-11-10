"""SecOps scans as verification checks (services.evidence.secops_checks)."""

from services.evidence import secops_checks


def test_clean_scans_pass():
    evidence = {
        "dependency_audit": {"tool": "pip-audit", "vulnerabilities": []},
        "secret_scan": {"tool": "gitleaks", "findings": []},
        "sast": {"tool": "bandit", "findings": []},
    }
    checks = secops_checks.check_all(evidence)
    assert len(checks) == 3
    assert all(c["outcome"] == "pass" for c in checks)


def test_a_vulnerability_refutes():
    evidence = {
        "dependency_audit": {
            "tool": "pip-audit",
            "vulnerabilities": [{"id": "CVE-1", "package": "x", "severity": "high"}],
        },
    }
    checks = secops_checks.check_all(evidence)
    assert len(checks) == 1
    assert checks[0]["outcome"] == "refuted"
    assert checks[0]["scan"] == "dependencies"


def test_missing_scans_are_skipped():
    assert secops_checks.check_all({}) == []
    assert secops_checks.check_all(None) == []
