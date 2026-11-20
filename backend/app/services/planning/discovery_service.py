"""
DiscoveryService — automated project stack detection and AI team composition.

Phase 1 (deterministic): Reads config files (package.json, pyproject.toml,
pom.xml, build.gradle, Gemfile, go.mod, etc.), counts source file extensions,
and inspects Dockerfiles / docker-compose.yml to build an accurate picture of
the technology stack.

Phase 2 (AI-powered): Sends the detected stack plus a compact snapshot of the
project's README / CONTRIBUTING to the LLM proxy so the Architect's model can
reason about the *actual* work involved and suggest a team with role-level
justifications. Falls back to deterministic rules if the LLM is unavailable.
Either way each agent's model is the deployment's default for its role
(``discovery_team.model_for_discovery_role``), never the suggestion's.

The stack detection is in ``stack_detection`` and the team's rules and
prompt in ``discovery_team``; this module re-exports their names.
"""

import json
import os
import re
from pathlib import Path

from models import ProjectDiscovery
from sqlmodel import Session

from services.planning.discovery_team import (
    _TEAM_PROMPT,
    DISCOVERY_ROLES,
    _build_team,
    model_for_discovery_role,
)
from services.planning.stack_detection import (  # noqa: F401 - re-exported
    _EXT_LANGUAGE,
    _SKIP_DIRS,
    _detect_databases,
    _detect_frameworks,
    _detect_infra,
    _detect_language,
    _estimate_complexity,
    _find_config_files,
    _read_json,
    _read_text,
    _walk_files,
)


def _read_readme(root: Path, max_chars: int = 1500) -> str:
    """Return the first *max_chars* characters of the project README, if any."""
    for name in ("README.md", "README.rst", "README.txt", "README"):
        candidate = root / name
        if candidate.exists():
            return _read_text(candidate)[:max_chars]
    return "(no README found)"


def _ai_suggest_team(
    detected_stack: dict,
    root: Path,
    llm_proxy_url: str,
) -> list[dict] | None:
    """
    Ask the LLM proxy to reason about the project and suggest a team.

    Workflow:
    1. Ask the Architect's default model for a JSON team: roles, seniority,
       specialty, system_prompt and reasoning (no models, and no model
       catalogue: the live catalogue is not read).
    2. Validate and normalise the response, setting every agent's ``model``
       to its role's default (``model_for_discovery_role``); a model the
       response names is ignored.

    Returns a list of agent dicts on success, or ``None`` on failure.
    """
    try:
        import logging

        from llm_proxy_client import LLMProxyClient

        log = logging.getLogger("fabrique-backend")

        client = LLMProxyClient(base_url=llm_proxy_url)
        # The team is parsed as JSON: thinking off (services/models/thinking.py).
        client.structured = True
        orchestrator_model = model_for_discovery_role("Architect")

        stack_summary = (
            f"Primary language: {detected_stack['language']}\n"
            f"All languages: {', '.join(f'{k} ({v} files)' for k, v in detected_stack.get('all_languages', {}).items())}\n"
            f"Frameworks: {', '.join(detected_stack['frameworks']) or 'none detected'}\n"
            f"Databases: {', '.join(detected_stack['databases']) or 'none detected'}\n"
            f"Infra: {', '.join(detected_stack['infra']) or 'none detected'}\n"
            f"Complexity: {detected_stack['complexity']} ({detected_stack['file_count']} files)"
        )
        readme = _read_readme(root)
        prompt = _TEAM_PROMPT.format(
            stack_summary=stack_summary,
            readme_excerpt=readme,
        )

        result = client.chat(
            messages=[{"role": "user", "content": prompt}],
            model=orchestrator_model,
            role="Architect",
        )

        if result.get("error"):
            log.warning("LLM team suggestion failed: %s", result["error"])
            return None

        content = (
            result.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
        )

        # Strip accidental markdown fences
        content = re.sub(r"^```(?:json)?\s*", "", content)
        content = re.sub(r"\s*```$", "", content)

        team = json.loads(content)
        if not isinstance(team, list) or not team:
            log.warning("LLM returned unexpected team shape: %s", type(team))
            return None

        valid_seniorities = {"Junior", "Mid", "Senior", "Staff"}

        normalised = []
        for agent in team:
            role = agent.get("role", "Dev")
            if role not in DISCOVERY_ROLES:
                role = "Dev"
            seniority = agent.get("seniority", "Senior")
            if seniority not in valid_seniorities:
                seniority = "Senior"
            specialty = str(agent.get("specialty", ""))[:80]
            normalised.append(
                {
                    "role": role,
                    "seniority": seniority,
                    "specialty": specialty,
                    # The deployment's default for the role, whatever the
                    # response named.
                    "model": model_for_discovery_role(role),
                    "system_prompt": str(agent.get("system_prompt", ""))[:1500],
                    "reasoning": str(agent.get("reasoning", ""))[:300],
                    "persona": specialty,
                }
            )

        log.info(
            "AI team: %d agents | %s",
            len(normalised),
            ", ".join(f"{a['role']}→{a['model']}" for a in normalised),
        )
        return normalised

    except Exception as exc:  # noqa: BLE001 - Fall back to the default team if AI discovery fails.
        import logging

        logging.getLogger("fabrique-backend").warning(
            "AI team suggestion error (will use rule-based fallback): %s", exc
        )
        return None


class DiscoveryService:
    """
    Scans a project directory and builds an accurate technology stack profile,
    then recommends an AI engineering team sized and specialised for that stack.

    Phase 1 is deterministic (filesystem walk + config-file parsing).
    Phase 2 uses the LLM proxy to reason about the project and produce a team
    with per-role justifications.  If the LLM is unavailable the service falls
    back to the rule-based ``_build_team()`` function automatically.

    Args:
        session:       Active SQLModel session used for persisting the discovery result.
        llm_proxy_url: Base URL of the internal LLM proxy (default: env var LLM_PROXY_URL).
    """

    def __init__(self, session: Session, llm_proxy_url: str | None = None):
        self.session = session
        self.llm_proxy_url = llm_proxy_url or os.getenv(
            "LLM_PROXY_URL", "http://localhost:5100"
        )

    def discover_project_team(
        self,
        company_id: int | None,
        project_path: str,
        stored_path: str | None = None,
        project_name: str | None = None,
    ):
        """
        Scan a project path and recommend an AI engineering team.

        Phase 1 — deterministic scan:
        - Walks up to 5 000 source files to determine the dominant language.
        - Reads package.json, pyproject.toml, pom.xml, Gemfile, go.mod, etc.
        - Inspects docker-compose.yml and .env for database services.
        - Detects CI, IaC, and containerisation tooling.

        Phase 2 — AI suggestion:
        - Sends the detected stack + README excerpt to the LLM proxy.
        - LLM returns a team with per-role reasoning.
        - Falls back to rule-based team if LLM is unavailable.

        Args:
            company_id:   Pass ``None`` for pre-creation discovery (no DB write).
            project_path: Filesystem path to the project root to analyse.
            stored_path:  The project's path on the user's machine when
                          *project_path* is a local mirror of it (a daemon
                          snapshot); recorded instead of *project_path*.
            project_name: The project's folder name, for the same case.

        Returns:
            Tuple of ``(discovery_orm_row_or_none, detected_stack_dict, suggested_team_list)``.
        """
        root = Path(project_path).expanduser().resolve()

        if not root.is_dir():
            raise ValueError(
                f"Project path does not exist or is not a directory: {root}"
            )

        folder_name = project_name or root.name or "New Project"

        # ── Phase 1: deterministic filesystem scan ────────────────────────────
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

        # ── Phase 2: AI team suggestion (with rule-based fallback) ────────────
        suggested_team = _ai_suggest_team(detected_stack, root, self.llm_proxy_url)
        if suggested_team is None:
            suggested_team = _build_team(
                primary_lang, frameworks, databases, infra, complexity
            )
            # Tag as rule-based so the frontend can show a different indicator
            detected_stack["team_source"] = "rules"
        else:
            detected_stack["team_source"] = "ai"

        discovery = None
        if company_id:
            discovery = ProjectDiscovery(
                company_id=company_id,
                project_path=stored_path or str(root),
                detected_stack=json.dumps(detected_stack),
                suggested_team=json.dumps(suggested_team),
                status="completed",
            )
            self.session.add(discovery)
            self.session.commit()
            self.session.refresh(discovery)

        return discovery, detected_stack, suggested_team
