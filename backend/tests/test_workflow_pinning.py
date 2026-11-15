"""Supply chain in CI (issue #522): every third-party action is pinned by commit.

A tag such as ``actions/checkout@v7`` is a pointer its owner can move; a
compromised or careless release then runs inside our CI with our token. A full
40-character commit sha cannot move, and Dependabot's ``github-actions``
ecosystem still bumps it (the version stays in a trailing comment).

These tests read every workflow under ``.github/workflows/`` and fail on a
``uses:`` of a step or a reusable workflow that names a third-party action by
anything but a commit sha. Local actions (``./...``) and ``docker://`` images
are not GitHub actions and are exempt. They also hold the dependency audit and
the pull-request scans in ``security-scan.yml`` to what makes them able to
fail.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
WORKFLOWS = REPO / ".github" / "workflows"
DEPENDABOT = REPO / ".github" / "dependabot.yml"
AUDIT_IGNORE = REPO / ".github" / "pip-audit-ignore.txt"

SHA = re.compile(r"^[0-9a-f]{40}$")

# The one reference that may stay on a tag, with the reason. The SLSA
# generator checks its own ref and refuses to run unless its reusable workflow
# is called by a full `@vX.Y.Z` release tag, so a sha would break provenance.
SLSA = (
    "slsa-framework/slsa-github-generator/.github/workflows/generator_generic_slsa3.yml"
)
TAG_ALLOWED = {
    SLSA: re.compile(r"^v\d+\.\d+\.\d+$"),
}


def _workflow_files() -> list[Path]:
    files = sorted([*WORKFLOWS.glob("*.yml"), *WORKFLOWS.glob("*.yaml")])
    assert files, "no workflow found"
    return files


def _uses(data: dict) -> list[str]:
    """Every ``uses:`` in a workflow: reusable workflows at job level, actions in steps."""
    found = []
    for job in (data.get("jobs") or {}).values():
        if "uses" in job:
            found.append(job["uses"])
        for step in job.get("steps") or []:
            if "uses" in step:
                found.append(step["uses"])
    return found


def _is_exempt(ref: str) -> bool:
    return ref.startswith(("./", "docker://"))


def _unpinned(ref: str) -> str | None:
    """Why ``ref`` is not pinned, or None when it is."""
    if _is_exempt(ref):
        return None
    if "@" not in ref:
        return "no ref"
    action, version = ref.rsplit("@", 1)
    if SHA.match(version):
        return None
    allowed = TAG_ALLOWED.get(action)
    if allowed and allowed.match(version):
        return None
    return f"pinned to {version!r}, not a 40-character commit sha"


@pytest.mark.parametrize("path", _workflow_files(), ids=lambda p: p.name)
def test_every_third_party_action_is_pinned_by_commit_sha(path):
    data = yaml.safe_load(path.read_text())
    problems = [f"{ref}: {why}" for ref in _uses(data) if (why := _unpinned(ref))]
    assert not problems, f"{path.name}: " + "; ".join(problems)


@pytest.mark.parametrize("path", _workflow_files(), ids=lambda p: p.name)
def test_every_sha_pin_names_its_version_in_a_comment(path):
    # The comment is what Dependabot updates beside the sha and what a reviewer
    # reads; a bare sha says nothing about which release it is.
    for line in path.read_text().splitlines():
        match = re.search(r"uses:\s*(\S+@([0-9a-f]{40}))(.*)$", line)
        if match:
            assert re.match(r"\s+#\s*v\d", match.group(3)), line.strip()


@pytest.mark.parametrize(
    ("ref", "pinned"),
    [
        ("actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1", True),
        ("actions/checkout@v7", False),
        ("actions/checkout@v7.0.1", False),
        ("actions/checkout@main", False),
        ("actions/checkout@3d3c42e", False),
        ("actions/checkout", False),
        ("github/codeql-action/upload-sarif@v4", False),
        ("owner/repo/.github/workflows/x.yml@v1.2.3", False),
        (f"{SLSA}@v2.1.0", True),
        (f"{SLSA}@v2", False),
        ("./.github/actions/local", True),
        ("docker://rhysd/actionlint:1.7.7", True),
    ],
)
def test_the_check_tells_a_pin_from_a_tag(ref, pinned):
    assert (_unpinned(ref) is None) is pinned


def test_dependabot_keeps_the_pins_up_to_date():
    data = yaml.safe_load(DEPENDABOT.read_text())
    ecosystems = {(u["package-ecosystem"], u["directory"]) for u in data["updates"]}
    assert ("github-actions", "/") in ecosystems


# --- the scans in security-scan.yml can fail ---------------------------------------


def _security_scan() -> dict:
    data = yaml.safe_load((WORKFLOWS / "security-scan.yml").read_text())
    # YAML 1.1 reads the bare key `on` as the boolean true.
    data["on"] = data.pop(True, data.get("on"))
    return data


def _run(job: dict) -> str:
    return "\n".join(str(step.get("run", "")) for step in job["steps"])


def test_no_security_step_swallows_its_failure():
    data = _security_scan()
    for name in ("dependencies", "secrets-pr", "sast-pr"):
        job = data["jobs"][name]
        assert "continue-on-error" not in job, name
        for step in job["steps"]:
            assert "continue-on-error" not in step, (name, step.get("name"))
            assert "|| true" not in str(step.get("run", "")), (name, step.get("name"))
    assert "safety check || true" not in (WORKFLOWS / "security-scan.yml").read_text()


def test_the_dependency_audit_covers_both_lockfiles_and_the_frontend():
    job = _security_scan()["jobs"]["dependencies"]
    text = _run(job)
    assert "for project in backend llm-proxy" in text
    assert "pip-audit --requirement" in text and "--require-hashes" in text
    assert "--ignore-vuln" in text and ".github/pip-audit-ignore.txt" in text
    assert "npm audit --package-lock-only --omit=dev --audit-level=high" in text


def test_the_pull_request_scans_run_on_pull_requests_and_the_self_hosted_runner():
    data = _security_scan()
    assert "pull_request" in data["on"]
    for name in ("secrets-pr", "sast-pr"):
        job = data["jobs"][name]
        assert job["if"] == "github.event_name == 'pull_request'"
        assert job["runs-on"] == ["self-hosted", "linux", "x64", "lafabrique"]
    gitleaks = _run(data["jobs"]["secrets-pr"])
    assert '--log-opts "${BASE_SHA}..${HEAD_SHA}"' in gitleaks
    assert "sha256sum -c -" in gitleaks and "--exit-code 1" in gitleaks
    semgrep = _run(data["jobs"]["sast-pr"])
    assert '--baseline-commit "$BASE_SHA"' in semgrep
    for flag in ("--config p/security-audit", "--severity ERROR", "--error"):
        assert flag in semgrep


def test_every_audit_exception_has_a_reason_and_a_valid_review_date():
    for raw in AUDIT_IGNORE.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 2)
        assert len(parts) == 3, f"needs ID, review date and reason: {line!r}"
        date.fromisoformat(parts[1])
