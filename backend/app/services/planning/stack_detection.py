"""Phase 1 of the project discovery: the stack, read from the files alone.

Walks the project (skipping vendor and build trees), counts the source
languages, reads the configuration files for frameworks, databases and
infrastructure, and estimates the complexity. Deterministic: no model, no
network. ``discovery_service`` re-exports every name.
"""

import json
import os
import re
from collections import Counter
from collections.abc import Iterable
from pathlib import Path

# Directories that should never be walked (vendor code, build artefacts, VCS).
_SKIP_DIRS = {
    ".git",
    ".svn",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    "env",
    "dist",
    "build",
    ".next",
    ".nuxt",
    "target",
    ".gradle",
    ".idea",
    ".vscode",
    "coverage",
    ".pytest_cache",
    ".mypy_cache",
    "vendor",
}

# Extension → canonical language name.
_EXT_LANGUAGE: dict[str, str] = {
    ".py": "Python",
    ".pyw": "Python",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".mjs": "JavaScript",
    ".java": "Java",
    ".kt": "Kotlin",
    ".kts": "Kotlin",
    ".go": "Go",
    ".rs": "Rust",
    ".rb": "Ruby",
    ".php": "PHP",
    ".cs": "C#",
    ".cpp": "C++",
    ".cc": "C++",
    ".cxx": "C++",
    ".c": "C",
    ".swift": "Swift",
    ".scala": "Scala",
    ".ex": "Elixir",
    ".exs": "Elixir",
    ".dart": "Dart",
}


def _walk_files(root: Path, max_files: int = 5000) -> list[Path]:
    """Yield up to *max_files* paths, skipping ignored directories."""
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        # Prune skipped directories in-place so os.walk doesn't descend.
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for name in filenames:
            files.append(Path(dirpath) / name)
            if len(files) >= max_files:
                return files
    return files


def _detect_language(files: list[Path]) -> tuple[str, Counter]:
    """Return the dominant language and the full extension counter."""
    ext_counts: Counter = Counter()
    for f in files:
        lang = _EXT_LANGUAGE.get(f.suffix.lower())
        if lang:
            ext_counts[lang] += 1
    if not ext_counts:
        return "Unknown", ext_counts
    return ext_counts.most_common(1)[0][0], ext_counts


def has_code_files(paths: Iterable[str]) -> bool:
    """Whether any of *paths* is a source file, by the extension map above.

    Reads the relative file paths of the daemon's project snapshot for the
    foundation-sprint decision (``services/loop/foundation_setup.py``,
    retrospective F8): deterministic, from the list alone — no model, no
    network, no filesystem of this server.
    """
    return any(
        isinstance(p, str) and Path(p).suffix.lower() in _EXT_LANGUAGE for p in paths
    )


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    except Exception:  # noqa: BLE001 - Unreadable discovery inputs are treated as unavailable.
        return {}


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except Exception:  # noqa: BLE001 - Unreadable discovery inputs are treated as unavailable.
        return ""


def _find_config_files(root: Path, filename: str, max_depth: int = 3) -> list[Path]:
    """
    Search for *filename* within *max_depth* directory levels below root.
    Stops at directories in _SKIP_DIRS to avoid scanning vendor trees.
    """
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        # Compute current depth relative to root
        depth = len(Path(dirpath).relative_to(root).parts)
        if depth >= max_depth:
            dirnames.clear()
            continue
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        if filename in filenames:
            found.append(Path(dirpath) / filename)
    return found


def _detect_frameworks(root: Path, primary_lang: str) -> list[str]:
    """Detect frameworks by inspecting config files up to 3 levels deep."""
    frameworks: list[str] = []

    # ── JavaScript / TypeScript ──────────────────────────────────────────────
    for pkg_path in _find_config_files(root, "package.json"):
        data = _read_json(pkg_path)
        deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
        if "react" in deps and "React" not in frameworks:
            frameworks.append("React")
        if "vue" in deps and "Vue" not in frameworks:
            frameworks.append("Vue")
        if "svelte" in deps and "Svelte" not in frameworks:
            frameworks.append("Svelte")
        if "next" in deps and "Next.js" not in frameworks:
            frameworks.append("Next.js")
        if "nuxt" in deps and "Nuxt.js" not in frameworks:
            frameworks.append("Nuxt.js")
        if "express" in deps and "Express" not in frameworks:
            frameworks.append("Express")
        if "fastify" in deps and "Fastify" not in frameworks:
            frameworks.append("Fastify")
        if ("nestjs" in deps or "@nestjs/core" in deps) and "NestJS" not in frameworks:
            frameworks.append("NestJS")
        if (
            "angular" in deps or "@angular/core" in deps
        ) and "Angular" not in frameworks:
            frameworks.append("Angular")
        if "vite" in deps and "Vite" not in frameworks:
            frameworks.append("Vite")

    # ── Python ───────────────────────────────────────────────────────────────
    for pyproject_path in _find_config_files(root, "pyproject.toml"):
        content = _read_text(pyproject_path).lower()
        if "fastapi" in content and "FastAPI" not in frameworks:
            frameworks.append("FastAPI")
        if "django" in content and "Django" not in frameworks:
            frameworks.append("Django")
        if "flask" in content and "Flask" not in frameworks:
            frameworks.append("Flask")
        if (
            "langgraph" in content or "langchain" in content
        ) and "LangGraph/LangChain" not in frameworks:
            frameworks.append("LangGraph/LangChain")
        if "sqlalchemy" in content and "SQLAlchemy" not in frameworks:
            frameworks.append("SQLAlchemy")

    for req_path in _find_config_files(root, "requirements.txt"):
        content = _read_text(req_path).lower()
        if "fastapi" in content and "FastAPI" not in frameworks:
            frameworks.append("FastAPI")
        if "django" in content and "Django" not in frameworks:
            frameworks.append("Django")
        if "flask" in content and "Flask" not in frameworks:
            frameworks.append("Flask")
        if (
            "langgraph" in content or "langchain" in content
        ) and "LangGraph/LangChain" not in frameworks:
            frameworks.append("LangGraph/LangChain")

    # ── Java / Kotlin ─────────────────────────────────────────────────────────
    for pom_path in _find_config_files(root, "pom.xml"):
        content = _read_text(pom_path)
        if (
            "spring-boot" in content or "spring-web" in content
        ) and "Spring Boot" not in frameworks:
            frameworks.append("Spring Boot")

    for gradle_name in ("build.gradle", "build.gradle.kts"):
        for gradle_path in _find_config_files(root, gradle_name):
            content = _read_text(gradle_path)
            if "spring" in content.lower() and "Spring Boot" not in frameworks:
                frameworks.append("Spring Boot")

    # ── Ruby ──────────────────────────────────────────────────────────────────
    for gemfile_path in _find_config_files(root, "Gemfile"):
        content = _read_text(gemfile_path)
        if "rails" in content.lower() and "Ruby on Rails" not in frameworks:
            frameworks.append("Ruby on Rails")

    # ── Go ────────────────────────────────────────────────────────────────────
    for gomod_path in _find_config_files(root, "go.mod"):
        content = _read_text(gomod_path)
        if "gin-gonic" in content and "Gin" not in frameworks:
            frameworks.append("Gin")
        if "labstack/echo" in content and "Echo" not in frameworks:
            frameworks.append("Echo")
        if "gofiber" in content and "Fiber" not in frameworks:
            frameworks.append("Fiber")

    return list(dict.fromkeys(frameworks))  # preserve first-seen order, no duplicates


def _detect_databases(root: Path) -> list[str]:
    """Scan config files (root + subdirs) and docker-compose for database clues."""
    databases: set[str] = set()
    hints = [
        (r"postgres|postgresql|pgvector", "PostgreSQL"),
        (r"mysql|mariadb", "MySQL"),
        (r"mongodb|mongoose|motor", "MongoDB"),
        (r"redis", "Redis"),
        (r"neo4j", "Neo4j"),
        (r"sqlite", "SQLite"),
        (r"elasticsearch|opensearch", "Elasticsearch"),
        (r"cassandra", "Cassandra"),
        (r"dynamodb", "DynamoDB"),
    ]
    # Build candidate file list: root-level configs + known sub-configs
    scan_paths: list[Path] = [
        root / "docker-compose.yml",
        root / "docker-compose.yaml",
        root / ".env",
        root / ".env.example",
        *_find_config_files(root, "pyproject.toml"),
        *_find_config_files(root, "requirements.txt"),
        *_find_config_files(root, "package.json"),
        *_find_config_files(root, "application.yml"),
        *_find_config_files(root, "application.properties"),
        *_find_config_files(root, "database.yml"),
        *_find_config_files(root, ".env"),
        *_find_config_files(root, ".env.example"),
    ]
    for path in scan_paths:
        if not path.exists() or not path.is_file():
            continue
        content = _read_text(path).lower()
        for pattern, db_name in hints:
            if re.search(pattern, content):
                databases.add(db_name)
    return sorted(databases)


def _detect_infra(root: Path) -> dict:
    """Detect containerisation, CI, cloud, and IaC tooling."""
    infra: dict[str, bool] = {}
    infra["docker"] = (root / "Dockerfile").exists() or any(root.glob("*/Dockerfile"))
    infra["docker_compose"] = (root / "docker-compose.yml").exists() or (
        root / "docker-compose.yaml"
    ).exists()
    infra["kubernetes"] = any(root.glob("**/*.yaml")) and any(
        "kind:" in _read_text(p)
        for p in root.glob("**/*.yaml")
        if "k8s" in str(p) or "kubernetes" in str(p)
    )
    infra["github_actions"] = (root / ".github" / "workflows").is_dir()
    infra["terraform"] = any(root.glob("**/*.tf"))
    infra["helm"] = any(root.glob("**/Chart.yaml"))
    return {k: v for k, v in infra.items() if v}


def _estimate_complexity(files: list[Path], lang_counts: Counter) -> str:
    """Estimate project complexity from file count and language diversity."""
    total = len(files)
    n_langs = len(lang_counts)
    if total > 500 or n_langs >= 4:
        return "High"
    if total > 100 or n_langs >= 2:
        return "Medium"
    return "Low"
