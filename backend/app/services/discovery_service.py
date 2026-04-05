"""
DiscoveryService — automated project stack detection and AI team composition.

Performs real filesystem introspection: reads config files (package.json,
pyproject.toml, pom.xml, build.gradle, Gemfile, go.mod, etc.), counts source
file extensions, and inspects Dockerfiles / docker-compose.yml to build an
accurate picture of the technology stack.  The detected stack is then used to
compose an optimal AI engineering team with appropriate roles and specialties.
"""

import json
import os
import re
from collections import Counter
from pathlib import Path
from typing import Optional

from sqlmodel import Session

from models import ProjectDiscovery

# Directories that should never be walked (vendor code, build artefacts, VCS).
_SKIP_DIRS = {
    ".git", ".svn", "node_modules", "__pycache__", ".venv", "venv", "env",
    "dist", "build", ".next", ".nuxt", "target", ".gradle", ".idea",
    ".vscode", "coverage", ".pytest_cache", ".mypy_cache", "vendor",
}

# Extension → canonical language name.
_EXT_LANGUAGE: dict[str, str] = {
    ".py": "Python", ".pyw": "Python",
    ".ts": "TypeScript", ".tsx": "TypeScript",
    ".js": "JavaScript", ".jsx": "JavaScript", ".mjs": "JavaScript",
    ".java": "Java",
    ".kt": "Kotlin", ".kts": "Kotlin",
    ".go": "Go",
    ".rs": "Rust",
    ".rb": "Ruby",
    ".php": "PHP",
    ".cs": "C#",
    ".cpp": "C++", ".cc": "C++", ".cxx": "C++",
    ".c": "C",
    ".swift": "Swift",
    ".scala": "Scala",
    ".ex": "Elixir", ".exs": "Elixir",
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


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return {}


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""


def _detect_frameworks(root: Path, primary_lang: str) -> list[str]:
    """Detect frameworks by inspecting well-known config files."""
    frameworks: list[str] = []

    pkg = root / "package.json"
    if pkg.exists():
        data = _read_json(pkg)
        deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
        if "react" in deps:
            frameworks.append("React")
        if "vue" in deps:
            frameworks.append("Vue")
        if "svelte" in deps:
            frameworks.append("Svelte")
        if "next" in deps:
            frameworks.append("Next.js")
        if "nuxt" in deps:
            frameworks.append("Nuxt.js")
        if "express" in deps:
            frameworks.append("Express")
        if "fastify" in deps:
            frameworks.append("Fastify")
        if "nestjs" in deps or "@nestjs/core" in deps:
            frameworks.append("NestJS")
        if "angular" in deps or "@angular/core" in deps:
            frameworks.append("Angular")

    pyproject = root / "pyproject.toml"
    requirements = root / "requirements.txt"
    if pyproject.exists():
        content = _read_text(pyproject)
        if "fastapi" in content:
            frameworks.append("FastAPI")
        if "django" in content:
            frameworks.append("Django")
        if "flask" in content:
            frameworks.append("Flask")
        if "langgraph" in content or "langchain" in content:
            frameworks.append("LangGraph/LangChain")
    if requirements.exists():
        content = _read_text(requirements).lower()
        if "fastapi" in content and "FastAPI" not in frameworks:
            frameworks.append("FastAPI")
        if "django" in content and "Django" not in frameworks:
            frameworks.append("Django")
        if "flask" in content and "Flask" not in frameworks:
            frameworks.append("Flask")

    for pom in (root / "pom.xml", *root.glob("*/pom.xml")):
        if pom.exists():
            content = _read_text(pom)
            if "spring-boot" in content or "spring-web" in content:
                frameworks.append("Spring Boot")
            break

    for gradle in (root / "build.gradle", root / "build.gradle.kts"):
        if gradle.exists():
            content = _read_text(gradle)
            if "spring" in content.lower():
                frameworks.append("Spring Boot")
            break

    gemfile = root / "Gemfile"
    if gemfile.exists():
        content = _read_text(gemfile)
        if "rails" in content.lower():
            frameworks.append("Ruby on Rails")

    go_mod = root / "go.mod"
    if go_mod.exists():
        content = _read_text(go_mod)
        if "gin-gonic" in content:
            frameworks.append("Gin")
        if "echo" in content:
            frameworks.append("Echo")
        if "fiber" in content:
            frameworks.append("Fiber")

    return list(dict.fromkeys(frameworks))  # deduplicate, preserve order


def _detect_databases(root: Path) -> list[str]:
    """Scan config files and docker-compose for database clues."""
    databases: set[str] = set()
    hints = [
        (r"postgres|postgresql|pgvector", "PostgreSQL"),
        (r"mysql|mariadb", "MySQL"),
        (r"mongodb|mongoose|motor", "MongoDB"),
        (r"redis", "Redis"),
        (r"neo4j", "Neo4j"),
        (r"sqlite", "SQLite"),
        (r"elasticsearch", "Elasticsearch"),
        (r"cassandra", "Cassandra"),
        (r"dynamodb", "DynamoDB"),
    ]
    scan_files = [
        root / "docker-compose.yml", root / "docker-compose.yaml",
        root / ".env", root / ".env.example",
        root / "pyproject.toml", root / "requirements.txt",
        root / "package.json",
        *root.glob("**/application.yml"), *root.glob("**/application.properties"),
    ]
    for path in scan_files:
        if not path.exists():
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
    infra["docker_compose"] = (root / "docker-compose.yml").exists() or (root / "docker-compose.yaml").exists()
    infra["kubernetes"] = any(root.glob("**/*.yaml")) and any(
        "kind:" in _read_text(p) for p in root.glob("**/*.yaml") if "k8s" in str(p) or "kubernetes" in str(p)
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


def _build_team(
    primary_lang: str,
    frameworks: list[str],
    databases: list[str],
    infra: dict,
    complexity: str,
) -> list[dict]:
    """
    Compose an AI engineering team tailored to the detected stack.

    Rules:
    - Always include PO, Architect, and at least one Developer.
    - Add a DevOps agent when Docker/K8s/Terraform is detected.
    - Add a Data/ML agent when AI frameworks (LangGraph, etc.) are present.
    - Scale to Staff seniority for High complexity.
    - Specialties are derived from actual detected technologies.
    """
    seniority_map = {"High": "Staff", "Medium": "Senior", "Low": "Mid"}
    dev_seniority = seniority_map[complexity]

    # Build specialty strings from real detections.
    fw_str = "/".join(frameworks[:2]) if frameworks else primary_lang
    db_str = "/".join(databases[:2]) if databases else "SQL"
    backend_langs = {"Python", "Java", "Go", "Rust", "Ruby", "PHP", "C#", "Kotlin", "Scala"}
    frontend_langs = {"TypeScript", "JavaScript"}

    team = [
        {"role": "PO", "seniority": "Senior", "specialty": "Product Management & Backlog"},
        {"role": "Architect", "seniority": dev_seniority, "specialty": f"{fw_str}/{db_str} Architecture"},
    ]

    # Frontend developer if JS/TS is present
    if primary_lang in frontend_langs or any(f in frameworks for f in ("React", "Vue", "Angular", "Svelte", "Next.js")):
        team.append({"role": "Dev", "seniority": dev_seniority, "specialty": f"Frontend/{fw_str}"})

    # Backend developer
    if primary_lang in backend_langs or any(f in frameworks for f in ("FastAPI", "Django", "Flask", "Spring Boot", "Express", "NestJS")):
        be_specialty = f"Backend/{primary_lang}" + (f"/{frameworks[0]}" if frameworks else "")
        team.append({"role": "Dev", "seniority": dev_seniority, "specialty": be_specialty})
    elif primary_lang not in frontend_langs:
        # Catch-all backend if nothing matched above
        team.append({"role": "Dev", "seniority": dev_seniority, "specialty": f"Backend/{primary_lang}"})

    # AI/ML agent when AI frameworks detected
    if any(f in " ".join(frameworks) for f in ("LangGraph", "LangChain", "ML", "AI")):
        team.append({"role": "Dev", "seniority": "Staff", "specialty": "AI/ML Engineering"})

    # DevOps when infrastructure tooling detected
    if infra.get("docker") or infra.get("kubernetes") or infra.get("terraform"):
        infra_specialty = "/".join(k for k in ("Docker", "K8s", "Terraform") if infra.get(k.lower().replace("k8s", "kubernetes")))
        team.append({"role": "DevOps", "seniority": dev_seniority, "specialty": infra_specialty or "Cloud Infrastructure"})

    # Always add QA
    team.append({"role": "QA", "seniority": "Senior", "specialty": "Automated Testing & Quality"})

    # Scrum Master only for High complexity
    if complexity == "High":
        team.append({"role": "SM", "seniority": "Senior", "specialty": "Agile Delivery"})

    return team


class DiscoveryService:
    """
    Scans a project directory and builds an accurate technology stack profile,
    then recommends an AI engineering team sized and specialised for that stack.

    Args:
        session: Active SQLModel session used for persisting the discovery result.
    """

    def __init__(self, session: Session):
        self.session = session

    def discover_project_team(self, company_id: Optional[int], project_path: str):
        """
        Scan a project path and recommend an AI engineering team.

        Performs real filesystem introspection:
        - Walks up to 5 000 source files to determine the dominant language.
        - Reads package.json, pyproject.toml, pom.xml, Gemfile, go.mod, etc.
        - Inspects docker-compose.yml and .env for database services.
        - Detects CI, IaC, and containerisation tooling.
        - Builds a team composition tailored to the detected stack.

        Args:
            company_id:   Pass ``None`` for pre-creation discovery (no DB write).
            project_path: Filesystem path to the project root to analyse.

        Returns:
            Tuple of ``(discovery_orm_row_or_none, detected_stack_dict, suggested_team_list)``.
        """
        root = Path(project_path).expanduser().resolve()

        # Validate the path exists and is a directory.
        if not root.is_dir():
            raise ValueError(f"Project path does not exist or is not a directory: {root}")

        folder_name = root.name or "New Project"

        # Walk the project tree.
        files = _walk_files(root)

        primary_lang, lang_counts = _detect_language(files)
        frameworks = _detect_frameworks(root, primary_lang)
        databases = _detect_databases(root)
        infra = _detect_infra(root)
        complexity = _estimate_complexity(files, lang_counts)

        detected_stack = {
            "name": folder_name,
            "language": primary_lang,
            "all_languages": dict(lang_counts.most_common(5)),
            "frameworks": frameworks,
            "databases": databases,
            "infra": list(infra.keys()),
            "complexity": complexity,
            "file_count": len(files),
        }

        suggested_team = _build_team(primary_lang, frameworks, databases, infra, complexity)

        discovery = None
        if company_id:
            discovery = ProjectDiscovery(
                company_id=company_id,
                project_path=str(root),
                detected_stack=json.dumps(detected_stack),
                suggested_team=json.dumps(suggested_team),
                status="completed",
            )
            self.session.add(discovery)
            self.session.commit()
            self.session.refresh(discovery)

        return discovery, detected_stack, suggested_team
