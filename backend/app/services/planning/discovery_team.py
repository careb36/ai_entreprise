"""The team a discovered project gets: the rules, and the model's prompt.

``_build_team`` composes the rule-based team from the detected stack (the
fallback when no model answers); ``_TEAM_PROMPT`` is what
``discovery_service`` sends the model to suggest one. The model proposes
roles, seniority, specialty, a system prompt and the reasoning, never the
models: every suggested agent's ``model`` is the deployment's default for its
role (``model_for_discovery_role``, through ``model_factory``, so
``AGENT_MODEL``, ``AGENT_MODEL_<ROLE>`` and ``NEMOTRON_ROLES`` still win), the
DeepSeek team of PR #739 when nothing is set. ``discovery_service``
re-exports every name.
"""

from services.models.model_factory import AgentRole, ModelFactory

# The roles discovery suggests, mapped to the factory's roles.
DISCOVERY_ROLES: dict[str, AgentRole] = {
    "PO": AgentRole.PO,
    "SM": AgentRole.SCRUM_MASTER,
    "Architect": AgentRole.ARCHITECT,
    "Dev": AgentRole.DEVELOPER,
    "QA": AgentRole.QA,
    "DevOps": AgentRole.DEVOPS,
    "Security": AgentRole.SECOPS,
    "Data": AgentRole.DATA_ENGINEER,
}


def model_for_discovery_role(role: str) -> str:
    """The deployment's default model for a discovery *role* (Dev when unknown)."""
    return ModelFactory.model_for_role(DISCOVERY_ROLES.get(role, AgentRole.DEVELOPER))


def with_default_models(team: list[dict]) -> list[dict]:
    """*team* with each agent's ``model`` set to its role's default."""
    return [
        {**agent, "model": model_for_discovery_role(agent["role"])} for agent in team
    ]


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
    backend_langs = {
        "Python",
        "Java",
        "Go",
        "Rust",
        "Ruby",
        "PHP",
        "C#",
        "Kotlin",
        "Scala",
    }
    frontend_langs = {"TypeScript", "JavaScript"}

    team = [
        {
            "role": "PO",
            "seniority": "Senior",
            "specialty": "Product Management & Backlog",
        },
        {
            "role": "Architect",
            "seniority": dev_seniority,
            "specialty": f"{fw_str}/{db_str} Architecture",
        },
    ]

    # Frontend developer if JS/TS is present
    if primary_lang in frontend_langs or any(
        f in frameworks for f in ("React", "Vue", "Angular", "Svelte", "Next.js")
    ):
        team.append(
            {
                "role": "Dev",
                "seniority": dev_seniority,
                "specialty": f"Frontend/{fw_str}",
            }
        )

    # Backend developer
    if primary_lang in backend_langs or any(
        f in frameworks
        for f in ("FastAPI", "Django", "Flask", "Spring Boot", "Express", "NestJS")
    ):
        be_specialty = f"Backend/{primary_lang}" + (
            f"/{frameworks[0]}" if frameworks else ""
        )
        team.append(
            {"role": "Dev", "seniority": dev_seniority, "specialty": be_specialty}
        )
    elif primary_lang not in frontend_langs:
        # Catch-all backend if nothing matched above
        team.append(
            {
                "role": "Dev",
                "seniority": dev_seniority,
                "specialty": f"Backend/{primary_lang}",
            }
        )

    # AI/ML agent when AI frameworks detected
    if any(f in " ".join(frameworks) for f in ("LangGraph", "LangChain", "ML", "AI")):
        team.append(
            {"role": "Dev", "seniority": "Staff", "specialty": "AI/ML Engineering"}
        )

    # DevOps when infrastructure tooling detected
    if infra.get("docker") or infra.get("kubernetes") or infra.get("terraform"):
        infra_specialty = "/".join(
            k
            for k in ("Docker", "K8s", "Terraform")
            if infra.get(k.lower().replace("k8s", "kubernetes"))
        )
        team.append(
            {
                "role": "DevOps",
                "seniority": dev_seniority,
                "specialty": infra_specialty or "Cloud Infrastructure",
            }
        )

    # Always add QA
    team.append(
        {
            "role": "QA",
            "seniority": "Senior",
            "specialty": "Automated Testing & Quality",
        }
    )

    # Scrum Master only for High complexity
    if complexity == "High":
        team.append(
            {"role": "SM", "seniority": "Senior", "specialty": "Agile Delivery"}
        )

    return with_default_models(team)


# ── LLM-powered team suggestion ───────────────────────────────────────────────

_TEAM_PROMPT = """\
You are an expert engineering manager at an AI software factory.

A new project has been scanned. Based on the stack context below, suggest the
optimal AI agent team to build and maintain this project.

## Detected Stack
{stack_summary}

## Project Context (README excerpt)
{readme_excerpt}

## Instructions
Return ONLY a valid JSON array — no markdown, no explanation outside the array.
Each element is an agent object with EXACTLY these fields:

- "role"         : one of PO, SM, Architect, Dev, QA, DevOps, Security, Data
- "seniority"    : one of Junior, Mid, Senior, Staff
- "specialty"    : concise 2-6 word focus area derived from the ACTUAL detected stack
- "system_prompt": A detailed paragraph (4-8 sentences) written in first person that defines:
                   * This agent's personality and communication style
                   * Their core responsibilities in THIS specific project
                   * The technologies they specialise in (from the detected stack)
                   * How they interact with other agents and escalate decisions
- "reasoning"    : one sentence explaining why THIS project needs this role

The factory assigns each role its model; do not name one.

Constraints:
- 3 to 8 agents maximum — do NOT pad with unnecessary roles
- Always include PO, Architect, and at least one Dev
- Use Staff seniority only for High complexity projects
- Do NOT add markdown fences or any text outside the JSON array
"""
