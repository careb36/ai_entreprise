"""Stack profiles: how to install, test, lint and start a project, per stack.

A profile is the per-repository setup a run performs before checking the
work, taken from GitHub Copilot coding agent's ``copilot-setup-steps.yml``,
OpenAI Codex cloud's environment setup script and Devin's per-repository
playbooks (how to install, run and test). Here it is detected from the
project's files, as the daemon's snapshot shows them, and overridable: the
deployment's ``DELIVERY_*`` settings, then the company's own
(``Company.stack_profile``, ``GET``/``PUT /v1/companies/{id}/stack_profile``),
most specific last. An empty string in an override turns that step off.

A profile is configuration, never evidence: what ran, and how it ended, is
the daemon's report (``services/app_check.py``).
"""

from __future__ import annotations

import fnmatch
import json
import os
from typing import Any

from config import settings

# The fields a profile carries, and the ones a company may override.
COMMAND_FIELDS = ("install", "test", "lint", "start", "e2e")
FIELDS = ("stack", *COMMAND_FIELDS, "port", "probe_path")

# Detected in this order: the first stack whose marker files exist wins.
# ``manifests`` are the files whose change calls for an install (exact names
# or patterns); ``markers`` the ones that say the stack is there.
PROFILES: tuple[dict[str, Any], ...] = (
    {
        "stack": "node",
        "markers": ("package.json",),
        "manifests": (
            "package.json",
            "package-lock.json",
            "npm-shrinkwrap.json",
            "yarn.lock",
            "pnpm-lock.yaml",
        ),
        "install": "npm install --no-audit --no-fund",
        "test": "npm test --silent",
        "lint": "npm run lint --if-present",
        "start": "npm start",
        "e2e": None,
        "port": 3000,
    },
    {
        "stack": "python",
        "markers": (
            "pyproject.toml",
            "requirements.txt",
            "setup.py",
            "setup.cfg",
            "Pipfile",
        ),
        "manifests": (
            "pyproject.toml",
            "requirements*.txt",
            "setup.py",
            "setup.cfg",
            "Pipfile",
            "Pipfile.lock",
            "poetry.lock",
        ),
        "install": "python -m pip install -r requirements.txt",
        "test": "python -m pytest -q",
        "lint": "python -m ruff check .",
        "start": None,
        "e2e": None,
        "port": 8000,
    },
    {
        "stack": "go",
        "markers": ("go.mod",),
        "manifests": ("go.mod", "go.sum"),
        "install": "go mod download",
        "test": "go test ./...",
        "lint": "go vet ./...",
        "start": "go run .",
        "e2e": None,
        "port": 8080,
    },
    {
        "stack": "rust",
        "markers": ("Cargo.toml",),
        "manifests": ("Cargo.toml", "Cargo.lock"),
        "install": "cargo fetch",
        "test": "cargo test",
        "lint": "cargo clippy -- -D warnings",
        "start": "cargo run",
        "e2e": None,
        "port": 8080,
    },
    {
        "stack": "java-maven",
        "markers": ("pom.xml",),
        "manifests": ("pom.xml",),
        "install": "mvn -q dependency:resolve",
        "test": "mvn -q test",
        "lint": "mvn -q -DskipTests verify",
        "start": "mvn -q spring-boot:run",
        "e2e": None,
        "port": 8080,
    },
    {
        "stack": "java-gradle",
        "markers": ("build.gradle", "build.gradle.kts"),
        "manifests": (
            "build.gradle",
            "build.gradle.kts",
            "settings.gradle",
            "settings.gradle.kts",
            "gradle.properties",
            "gradle/libs.versions.toml",
        ),
        "install": "gradle testClasses",
        "test": "gradle test",
        "lint": "gradle check -x test",
        "start": "gradle run",
        "e2e": None,
        "port": 8080,
    },
    {
        "stack": "dotnet",
        "markers": ("*.csproj", "*.fsproj", "*.sln"),
        "manifests": (
            "*.csproj",
            "*.fsproj",
            "*.sln",
            "Directory.Packages.props",
            "packages.lock.json",
        ),
        "install": "dotnet restore",
        "test": "dotnet test",
        "lint": "dotnet format --verify-no-changes",
        "start": "dotnet run --urls http://0.0.0.0:$PORT",
        "e2e": None,
        "port": 5000,
    },
    {
        "stack": "php",
        "markers": ("composer.json",),
        "manifests": ("composer.json", "composer.lock"),
        "install": "composer install --no-interaction",
        "test": "vendor/bin/phpunit",
        "lint": "composer validate --strict",
        "start": "php -S 0.0.0.0:$PORT -t public",
        "e2e": None,
        "port": 8000,
    },
    {
        "stack": "ruby",
        "markers": ("Gemfile",),
        "manifests": ("Gemfile", "Gemfile.lock"),
        "install": "bundle install",
        "test": "bundle exec rake test",
        "lint": "bundle exec rubocop",
        "start": None,
        "e2e": None,
        "port": 9292,
    },
)

_NPM_E2E_SCRIPTS = ("test:e2e", "e2e")


def _matches(relative: str, patterns: tuple[str, ...]) -> bool:
    """*relative* (a POSIX path) matches one of *patterns*: a pattern without
    a slash matches the file name at any depth, one with a slash the path."""
    name = relative.rsplit("/", 1)[-1]
    return any(fnmatch.fnmatchcase(relative if "/" in p else name, p) for p in patterns)


def manifest_patterns() -> tuple[str, ...]:
    """Every stack's dependency manifests, for telling an install is due
    without knowing the stack yet."""
    seen: list[str] = []
    for profile in PROFILES:
        for pattern in profile["manifests"]:
            if pattern not in seen:
                seen.append(pattern)
    return tuple(seen)


def is_manifest(path: str) -> bool:
    """Whether *path* is a dependency manifest of some stack."""
    return bool(path) and _matches(path.replace("\\", "/"), manifest_patterns())


def _root_files(root: str) -> list[str]:
    try:
        return sorted(os.listdir(root))
    except OSError:
        return []


def _read(root: str, name: str) -> str:
    try:
        with open(os.path.join(root, name), encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def _package_scripts(root: str) -> dict[str, Any]:
    try:
        scripts = json.loads(_read(root, "package.json") or "{}").get("scripts")
    except (ValueError, AttributeError):
        return {}
    return scripts if isinstance(scripts, dict) else {}


def _refine(profile: dict[str, Any], root: str, names: list[str]) -> dict[str, Any]:
    """What the project's own files say beyond the stack's defaults."""
    stack = profile["stack"]
    if stack == "node":
        scripts = _package_scripts(root)
        if not str(scripts.get("start") or "").strip():
            profile["start"] = None
        if not str(scripts.get("lint") or "").strip():
            profile["lint"] = None
        for script in _NPM_E2E_SCRIPTS:
            if str(scripts.get(script) or "").strip():
                profile["e2e"] = f"npm run {script} --silent"
                break
        if "pnpm-lock.yaml" in names:
            profile["install"] = "pnpm install --frozen-lockfile"
        elif "yarn.lock" in names:
            profile["install"] = "yarn install --frozen-lockfile"
        elif "package-lock.json" in names:
            profile["install"] = "npm ci --no-audit --no-fund"
    elif stack == "python":
        pyproject = _read(root, "pyproject.toml") if "pyproject.toml" in names else ""
        if "requirements.txt" in names:
            profile["install"] = "python -m pip install -r requirements.txt"
        elif "[tool.poetry]" in pyproject:
            profile["install"] = "poetry install"
        elif "[project]" in pyproject or "setup.py" in names:
            profile["install"] = "python -m pip install -e ."
        elif "Pipfile" in names:
            profile["install"] = "pipenv install --dev"
        if "manage.py" in names:
            profile["start"] = "python manage.py runserver 0.0.0.0:$PORT --noreload"
        else:
            for entry in ("app.py", "main.py", "server.py"):
                if entry in names:
                    profile["start"] = f"python {entry}"
                    break
    elif stack == "ruby":
        if os.path.isdir(os.path.join(root, "spec")):
            profile["test"] = "bundle exec rspec"
        if os.path.isfile(os.path.join(root, "bin", "rails")):
            profile["start"] = "bin/rails server -b 0.0.0.0 -p $PORT"
            profile["port"] = 3000
        elif "config.ru" in names:
            profile["start"] = "bundle exec rackup -o 0.0.0.0 -p $PORT"
    elif stack == "php" and "public" not in names:
        profile["start"] = "php -S 0.0.0.0:$PORT"
    return profile


def detect(root: str) -> dict[str, Any] | None:
    """The profile of the project in *root* (a folder, often the mirror of
    the daemon's snapshot), or ``None`` when no stack is recognised."""
    names = _root_files(root)
    for template in PROFILES:
        if any(_matches(name, template["markers"]) for name in names):
            profile = {k: v for k, v in template.items() if k != "markers"}
            profile["manifests"] = list(template["manifests"])
            profile["probe_path"] = "/"
            return _refine(profile, root, names)
    return None


def _deployment_overrides() -> dict[str, Any]:
    """The deployment's ``DELIVERY_*`` settings, where set."""
    out: dict[str, Any] = {}
    for field, value in (
        ("install", settings.DELIVERY_INSTALL_COMMAND),
        ("test", settings.DELIVERY_TEST_COMMAND),
        ("lint", settings.DELIVERY_LINT_COMMAND),
        ("start", settings.DELIVERY_START_COMMAND),
        ("e2e", settings.DELIVERY_E2E_COMMAND),
    ):
        if str(value or "").strip():
            out[field] = str(value).strip()
    if settings.DELIVERY_APP_PORT:
        out["port"] = int(settings.DELIVERY_APP_PORT)
    if str(settings.DELIVERY_APP_PROBE_PATH or "").strip() not in ("", "/"):
        out["probe_path"] = settings.DELIVERY_APP_PROBE_PATH.strip()
    return out


def validate_overrides(raw: Any) -> dict[str, Any]:
    """A company's overrides, checked. Raises ``ValueError`` with the reason.

    Commands are strings (empty turns the step off), ``port`` an integer
    between 1 and 65535, ``probe_path`` a path starting with ``/``; unknown
    keys are refused rather than ignored. Raises ``TypeError`` for a value
    that is not an object."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise TypeError("the stack profile must be an object")
    out: dict[str, Any] = {}
    for key, value in raw.items():
        if key not in FIELDS:
            raise ValueError(f"unknown stack profile field: {key}")
        if value is None:
            continue
        if key == "port":
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError("port must be an integer")
            if not 1 <= value <= 65535:
                raise ValueError("port must be between 1 and 65535")
            out[key] = value
            continue
        if not isinstance(value, str) or len(value) > 500 or "\n" in value:
            raise ValueError(f"{key} must be one line of text (at most 500 characters)")
        value = value.strip()
        if key == "probe_path" and not value.startswith("/"):
            raise ValueError("probe_path must start with /")
        if key == "stack" and value and value not in stack_names():
            raise ValueError(f"unknown stack: {value}")
        out[key] = value
    return out


def stack_names() -> list[str]:
    return [p["stack"] for p in PROFILES]


def template_for(stack: str) -> dict[str, Any] | None:
    for template in PROFILES:
        if template["stack"] == stack:
            profile = {k: v for k, v in template.items() if k != "markers"}
            profile["manifests"] = list(template["manifests"])
            profile["probe_path"] = "/"
            return profile
    return None


def resolve(detected: dict[str, Any] | None, company_overrides: Any) -> dict[str, Any]:
    """The profile to use: detected, then the deployment's settings, then the
    company's; an empty command means the step is off. ``source`` names where
    each field came from, so the evidence can say whose command ran."""
    try:
        own = validate_overrides(company_overrides)
    except (TypeError, ValueError):
        own = {}  # Refused when written; an old row never stops a run.
    profile: dict[str, Any] = dict(detected or {})
    if own.get("stack") and own["stack"] != profile.get("stack"):
        profile = template_for(own["stack"]) or profile
    source = {field: "detected" for field in FIELDS if profile.get(field)}
    for origin, overrides in (
        ("deployment", _deployment_overrides()),
        ("company", own),
    ):
        for field, value in overrides.items():
            profile[field] = value if value != "" else None
            source[field] = origin
    profile.setdefault("stack", None)
    profile.setdefault("manifests", list(manifest_patterns()))
    profile.setdefault("probe_path", "/")
    for field in COMMAND_FIELDS:
        profile.setdefault(field, None)
    profile.setdefault("port", None)
    profile["source"] = {k: v for k, v in source.items() if profile.get(k)}
    return profile
