"""The release's supply chain (issue #522, part C).

The daemon installs from a hash-pinned lock; a release carries an SBOM of each
component; the published images are signed by digest and carry their SBOM as
an attestation; the licence job scans the four components; ``CODEOWNERS``
names an owner for what decides security; the gitleaks pre-commit hook runs
the version CI runs. These tests read the files and hold them to that. The
signing steps themselves only run on a release tag (docs/OPERATIONS.md,
Releasing).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
WORKFLOWS = REPO / ".github" / "workflows"
DAEMON = REPO / "vscode-daemon"
SHA40 = re.compile(r"^[0-9a-f]{40}$")


def _workflow(name: str) -> dict:
    data = yaml.safe_load((WORKFLOWS / name).read_text())
    data["on"] = data.pop(True, data.get("on"))
    return data


def _run(job: dict) -> str:
    return "\n".join(str(step.get("run", "")) for step in job["steps"])


# --- the daemon's lock ---------------------------------------------------------------


def _requirements() -> list[str]:
    """The lock's requirement lines (``name==version ; marker \\``), no hashes."""
    return [
        line
        for line in (DAEMON / "requirements.txt").read_text().splitlines()
        if line and not line.startswith((" ", "#"))
    ]


def test_the_daemon_lock_pins_every_package_with_its_hashes():
    text = (DAEMON / "requirements.txt").read_text()
    assert "requirements.in" in text.splitlines()[1]  # the command that made it
    assert "--generate-hashes" in text.splitlines()[1]
    lines = _requirements()
    assert lines
    for line in lines:
        assert re.match(r"^[A-Za-z0-9._-]+==[^ ;]+( ;.*)? \\$", line), line
    blocks = re.split(r"\n(?=\S)", text)
    for block in blocks:
        if block.startswith("#") or not block.strip():
            continue
        assert re.search(r"--hash=sha256:[0-9a-f]{64}", block), block.splitlines()[0]
    names = {line.split("==")[0].lower() for line in lines}
    # Every direct dependency in requirements.in is in the lock.
    direct = {
        re.split(r"[<>=!~ ;]", line, maxsplit=1)[0].lower()
        for line in (DAEMON / "requirements.in").read_text().splitlines()
        if line.strip() and not line.startswith("#")
    }
    assert direct == {"nats-py", "nkeys"}
    assert direct <= names


@pytest.mark.parametrize(
    "path",
    [
        DAEMON / "Dockerfile",
        REPO / "scripts" / "start-daemon.sh",
        REPO / "scripts" / "start-daemon.ps1",
    ],
    ids=lambda p: p.name,
)
def test_every_daemon_install_requires_the_hashes(path):
    text = path.read_text()
    installs = [line for line in text.splitlines() if "pip install" in line]
    assert installs
    for line in installs:
        # Nothing is installed beside the lock: a bare `pip install x==1`
        # would skip the hash check for x.
        assert "--require-hashes" in line and "requirements.txt" in line, line


# --- SBOMs and signatures --------------------------------------------------------------

SYFT_URL = "https://github.com/anchore/syft/releases/download/v1.55.0/syft_1.55.0_linux_amd64.tar.gz"
SYFT_SHA = "bb65249542db5fedeeb4d3f9b018b75e7264dbfbbb10486f044581a92f90490d"

# The jobs of images.yml that sign what they push: the five-image matrix and
# the browser sandbox's linux/amd64 publish (issue #529).
_SIGNING_JOBS = ("publish", "publish-browser")


def test_the_release_attaches_an_sbom_of_each_component():
    job = _workflow("release.yml")["jobs"]["release"]
    text = _run(job)
    assert SYFT_URL in text and f'echo "{SYFT_SHA}  syft.tgz" | sha256sum -c -' in text
    for lock in (
        "backend:backend/poetry.lock",
        "llm-proxy:llm-proxy/poetry.lock",
        "vscode-daemon:vscode-daemon/requirements.txt",
        "frontend:frontend/package-lock.json",
    ):
        assert lock in text
    assert "cyclonedx-json=" in text and "SHA256SUMS" in text
    create = next(s for s in job["steps"] if s.get("name") == "Create Release")
    assert (
        '"$RUNNER_TEMP"/sbom/*.cdx.json "$RUNNER_TEMP/sbom/SHA256SUMS"' in create["run"]
    )
    names = [s.get("name") for s in job["steps"]]
    assert names.index("Write The SBOMs") < names.index("Create Release")


def test_images_are_signed_by_digest_and_carry_their_sbom():
    data = _workflow("images.yml")
    assert data["permissions"] == {"contents": "read"}
    # The OIDC token only where it signs: the five-image matrix and the
    # browser sandbox's linux/amd64 publish (issue #529).
    for name, job in data["jobs"].items():
        assert ("id-token" in (job.get("permissions") or {})) is (name in _SIGNING_JOBS)
    publish = data["jobs"]["publish"]
    assert publish["permissions"]["id-token"] == "write"
    steps = publish["steps"]
    installer = [
        s
        for s in steps
        if str(s.get("uses", "")).startswith("sigstore/cosign-installer@")
    ]
    assert len(installer) == 1
    assert SHA40.match(installer[0]["uses"].split("@")[1])
    assert re.match(r"^v\d+\.\d+\.\d+$", installer[0]["with"]["cosign-release"])
    text = _run(publish)
    assert SYFT_URL in text and SYFT_SHA in text
    assert re.search(r'grep -E "\^\$\{image\}@sha256:\[0-9a-f\]\{64\}\$"', text)
    assert 'cosign sign --yes "$IMAGE_REF"' in text
    assert "cosign attest --yes --type cyclonedx" in text
    # Never a tag: what is signed is the digest the registry stored.
    assert not re.search(r"cosign (sign|attest)[^\n]*:\$\{VERSION\}", text)
    names = [s.get("name", "") for s in steps]
    order = [
        next(i for i, n in enumerate(names) if n.startswith(prefix))
        for prefix in (
            "Push",
            "SBOM of",
            "Install cosign",
            "Sign",
            "Keep the SBOM",
            "Clean up",
        )
    ]
    assert order == sorted(order)
    keep = next(s for s in steps if s.get("name", "").startswith("Keep the SBOM"))
    assert keep["uses"].startswith("actions/upload-artifact@")
    assert keep["with"]["if-no-files-found"] == "error"


def test_the_browser_sandbox_publishes_amd64_signed_and_attested():
    """The sixth image (issue #529): buildx for linux/amd64 only, outside the
    five-image matrix, signed and attested as the five others. Its arm64
    variant's browser aborts under QEMU, so it is not published until it has
    been run on arm64 hardware."""
    job = _workflow("images.yml")["jobs"]["publish-browser"]
    assert job["needs"] == "version"
    assert job["permissions"] == {
        "contents": "read",
        "packages": "write",
        "id-token": "write",
    }
    uses = [s["uses"] for s in job["steps"] if "uses" in s]
    assert not any(u.startswith("docker/setup-qemu-action@") for u in uses)
    assert any(u.startswith("docker/setup-buildx-action@") for u in uses)
    text = _run(job)
    assert "--platform linux/amd64 " in text and "arm64" not in text
    assert "--push" in text
    assert "ghcr.io/${GITHUB_REPOSITORY,,}/${NAME}" in text
    # The digest signed is buildx's, from its metadata file: a manifest list
    # never reaches the Docker daemon, and a tag can be moved.
    assert "--metadata-file" in text and "containerimage.digest" in text
    assert SYFT_URL in text and SYFT_SHA in text
    assert 'cosign sign --yes "$IMAGE_REF"' in text
    assert "cosign attest --yes --type cyclonedx" in text
    assert not re.search(r"cosign (sign|attest)[^\n]*:\$\{VERSION\}", text)
    names = [s.get("name", "") for s in job["steps"]]
    order = [
        next(i for i, n in enumerate(names) if n.startswith(prefix))
        for prefix in (
            "Build and push",
            "SBOM of",
            "Install cosign",
            "Sign",
            "Keep the SBOM",
            "Clean up",
        )
    ]
    assert order == sorted(order)
    keep = next(
        s for s in job["steps"] if s.get("name", "").startswith("Keep the SBOM")
    )
    assert keep["uses"].startswith("actions/upload-artifact@")
    assert keep["with"]["if-no-files-found"] == "error"


def test_the_browser_sandbox_is_built_once_per_playwright_release():
    """Issue #315: the releases come from playwright_versions.json, checked by
    playwright_matrix.py in the version job; each build gets its values
    through the environment, is tagged with its Playwright and recorded by
    the digest the registry stored. Only the default release moves the
    plain version tag and ``latest``."""
    data = _workflow("images.yml")
    version = data["jobs"]["version"]
    assert version["outputs"]["browser_matrix"] == "${{ steps.browser.outputs.matrix }}"
    resolve = next(s for s in version["steps"] if s.get("id") == "browser")
    assert (
        "python3 -I vscode-daemon/images/browser/playwright_matrix.py" in resolve["run"]
    )
    assert "playwright_versions.json" in resolve["run"]
    job = data["jobs"]["publish-browser"]
    assert job["strategy"]["fail-fast"] is False
    assert job["strategy"]["matrix"] == {
        "include": "${{ fromJSON(needs.version.outputs.browser_matrix) }}"
    }
    env = job["env"]
    assert env["PLAYWRIGHT"] == "${{ matrix.playwright }}"
    assert env["LOCK_DIR"] == "${{ matrix.lockfile_dir }}"
    assert env["SHELL_SHA256_AMD64"] == "${{ matrix.sha256_amd64 }}"
    assert env["DEFAULT_RELEASE"] == "${{ matrix.default }}"
    text = _run(job)
    # No matrix value is pasted into a script (an injection would run there).
    assert "${{" not in text
    assert '--tag "${image}:${VERSION}-playwright-${PLAYWRIGHT}"' in text
    default_block = text.split('if [ "$DEFAULT_RELEASE" = "true" ]; then', 1)[1]
    assert default_block.index('"${image}:${VERSION}"') < default_block.index("fi")
    assert 'tags+=(--tag "${image}:latest")' in default_block
    for arg in (
        "PLAYWRIGHT_VERSION=${PLAYWRIGHT}",
        "PLAYWRIGHT_LOCK_DIR=${LOCK_DIR}",
        "HEADLESS_SHELL_REVISION=${SHELL_REVISION}",
        "HEADLESS_SHELL_VERSION=${SHELL_VERSION}",
        "HEADLESS_SHELL_SHA256_AMD64=${SHELL_SHA256_AMD64}",
    ):
        assert f'--build-arg "{arg}"' in text
    # Recorded by digest: the record file and the line operators paste.
    assert ".digest.json" in text and "DELIVERY_BROWSER_IMAGES" in text
    assert "${PLAYWRIGHT}=${image}@${digest}" in text
    keep = next(
        s for s in job["steps"] if s.get("name", "").startswith("Keep the SBOM")
    )
    assert (
        keep["with"]["name"]
        == "sbom-browser-sandbox-playwright-${{ matrix.playwright }}"
    )


# --- the browser image's manual probe (issue #529) ----------------------------------


def _probe_problems(data: dict) -> list[str]:
    """What breaks the probe's promise: run by hand only, with a read-only
    token, pushing nowhere, and running the arm64 image the way the daemon
    runs it (network off, capabilities dropped, an unprivileged uid)."""
    problems = []
    if set(data.get("on") or {}) != {"workflow_dispatch"}:
        problems.append("triggered by something other than workflow_dispatch")
    if data.get("permissions") != {"contents": "read"}:
        problems.append("the workflow token is not contents: read")
    for name, job in (data.get("jobs") or {}).items():
        if "permissions" in job:
            problems.append(f"job {name} widens its permissions")
        if job.get("runs-on") != ["self-hosted", "linux", "x64", "lafabrique"]:
            problems.append(f"job {name} is not on the lafabrique runner")
        text = _run(job)
        for forbidden in ("--push", "docker login", "docker push", "type=registry"):
            if forbidden in text:
                problems.append(f"job {name} runs {forbidden}")
        if "--platform linux/amd64,linux/arm64" not in text:
            problems.append(f"job {name} does not build both architectures")
        for flag in (
            "--platform linux/arm64",
            "--network none",
            "--cap-drop ALL",
            "--security-opt no-new-privileges",
            "--user 4321:4321",
            "node /opt/fabrique/probe-browser.js",
        ):
            if flag not in text:
                problems.append(f"job {name} runs the probe without {flag}")
    return problems


def _probe() -> dict:
    return _workflow("browser-image-probe.yml")


@pytest.mark.parametrize(
    "mutate, expected",
    [
        (lambda d: d["on"].update({"pull_request": None}), "workflow_dispatch"),
        (lambda d: d["on"].update({"push": {"tags": ["v*"]}}), "workflow_dispatch"),
        (lambda d: d.update({"permissions": {"contents": "write"}}), "contents: read"),
        (
            lambda d: d["jobs"]["probe"].update({"permissions": {"packages": "write"}}),
            "widens its permissions",
        ),
        (
            lambda d: d["jobs"]["probe"]["steps"].append(
                {"run": "docker buildx build --push ."}
            ),
            "runs --push",
        ),
        (
            lambda d: d["jobs"]["probe"]["steps"].append(
                {"run": "printf x | docker login ghcr.io --password-stdin"}
            ),
            "runs docker login",
        ),
        (lambda d: d["jobs"]["probe"].update({"runs-on": "ubuntu-latest"}), "runner"),
    ],
)
def test_the_browser_probe_check_refuses_a_probe_that_could_publish(mutate, expected):
    data = _probe()
    mutate(data)
    assert any(expected in p for p in _probe_problems(data)), _probe_problems(data)


def test_the_browser_probe_check_refuses_a_probe_run_without_the_sandbox_flags():
    data = _probe()
    for step in data["jobs"]["probe"]["steps"]:
        if "run" in step:
            step["run"] = step["run"].replace("--network none", "")
    assert any("--network none" in p for p in _probe_problems(data))


def test_the_browser_probe_is_manual_read_only_and_pushes_nowhere():
    """browser-image-probe.yml is the gate for an arm64 browser-sandbox: it
    builds both architectures with buildx under QEMU and runs the arm64 image
    once with its probe, without ever holding a token that can publish."""
    data = _probe()
    assert _probe_problems(data) == []
    uses = [s["uses"] for s in data["jobs"]["probe"]["steps"] if "uses" in s]
    assert any(u.startswith("docker/setup-qemu-action@") for u in uses)
    assert any(u.startswith("docker/setup-buildx-action@") for u in uses)
    assert all(SHA40.match(u.split("@")[1].split()[0]) for u in uses)
    assert "--output type=cacheonly" in _run(data["jobs"]["probe"])


def test_the_signing_is_documented_with_its_verification():
    ops = (REPO / "docs" / "OPERATIONS.md").read_text()
    assert "cosign verify" in ops and "cosign verify-attestation" in ops
    assert (
        "--certificate-oidc-issuer https://token.actions.githubusercontent.com" in ops
    )
    assert r"\.github/workflows/images\.yml@refs/tags/v" in ops


# --- the licence job -----------------------------------------------------------------


def test_the_licence_job_scans_the_four_components():
    job = _workflow("security-scan.yml")["jobs"]["licences"]
    assert job["runs-on"] == ["self-hosted", "linux", "x64", "lafabrique"]
    assert job["permissions"] == {"contents": "read"}
    text = _run(job)
    assert "--no-deps --require-hashes" in text
    assert "for project in backend llm-proxy vscode-daemon" in text
    assert "python3 -I scripts/licence_scan.py" in text
    for project in ("backend", "llm-proxy", "vscode-daemon"):
        assert f'--python "{project}=$RUNNER_TEMP/licence-{project}/bin/python"' in text
    assert "--npm-lock frontend/package-lock.json" in text
    # Runtime only: the export names no group, so it is the main group.
    assert "--all-groups" not in text and "--with" not in text


# --- CODEOWNERS and the pre-commit hook --------------------------------------------------


def _codeowners() -> dict[str, list[str]]:
    rules = {}
    for line in (REPO / ".github" / "CODEOWNERS").read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            pattern, *owners = line.split()
            rules[pattern] = owners
    return rules


def test_codeowners_covers_what_decides_security():
    rules = _codeowners()
    for pattern in (
        "/.github/",
        "/.github/workflows/",
        "/SECURITY.md",
        "/.pre-commit-config.yaml",
        "/.gitleaks.toml",
        "/vscode-daemon/",
        "/nats/",
        "/scripts/nats_config.py",
        "/backend/alembic/",
        "/backend/app/migrations.py",
        "/backend/app/security.py",
        "/backend/app/routers/auth.py",
        "/docs/architecture/",
    ):
        assert pattern in rules, pattern
    assert all(owners == ["@careb36"] for owners in rules.values())


def test_every_path_codeowners_names_exists():
    for pattern in _codeowners():
        if pattern == "*":
            continue
        if "*" in pattern:
            assert any(REPO.glob(pattern.lstrip("/"))), pattern
            continue
        assert (REPO / pattern.lstrip("/")).exists(), pattern


def test_the_gitleaks_hook_runs_the_version_ci_runs():
    config = yaml.safe_load((REPO / ".pre-commit-config.yaml").read_text())
    repo = next(
        r
        for r in config["repos"]
        if r["repo"] == "https://github.com/gitleaks/gitleaks"
    )
    assert SHA40.match(repo["rev"])
    hook = next(h for h in repo["hooks"] if h["id"] == "gitleaks")
    assert hook["args"] == ["--config", ".gitleaks.toml"]
    text = (REPO / ".pre-commit-config.yaml").read_text()
    version = re.search(r"# frozen: (v[\d.]+)", text).group(1)
    ci = (WORKFLOWS / "security-scan.yml").read_text()
    assert f"/releases/download/{version}/gitleaks_{version[1:]}_linux_x64" in ci
