"""The team a discovered project gets: the rules, and the model's prompt.

``_build_team`` composes the rule-based team from the detected stack (the
fallback when no model answers); ``_TEAM_PROMPT`` and
``_build_dynamic_catalog`` are what ``discovery_service`` sends the model to
suggest one. ``discovery_service`` re-exports every name.
"""


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

    return team


# ── LLM-powered team suggestion ───────────────────────────────────────────────

_TEAM_PROMPT = """\
You are an expert engineering manager at an AI software factory.

A new project has been scanned. Based on the stack context below, suggest the
optimal AI agent team to build and maintain this project.

## Detected Stack
{stack_summary}

## Project Context (README excerpt)
{readme_excerpt}

## Available Models (current live catalogue — choose ONLY from this list)
{model_catalog}

## Assignment Guidelines
- strategic tier  → PO, Architect  (complex decisions, stakeholder impact)
- balanced tier   → SM, Security   (coordination, reviews, risk analysis)
- code tier       → Dev, QA, DevOps, Data  (primary output is code/tests/scripts)
- fast/preview    → avoid unless no better option exists

## Instructions
Return ONLY a valid JSON array — no markdown, no explanation outside the array.
Each element is an agent object with EXACTLY these fields:

- "role"         : one of PO, SM, Architect, Dev, QA, DevOps, Security, Data
- "seniority"    : one of Junior, Mid, Senior, Staff
- "specialty"    : concise 2-6 word focus area derived from the ACTUAL detected stack
- "model"        : one model id from the catalogue above (must match exactly)
- "system_prompt": A detailed paragraph (4-8 sentences) written in first person that defines:
                   * This agent's personality and communication style
                   * Their core responsibilities in THIS specific project
                   * The technologies they specialise in (from the detected stack)
                   * How they interact with other agents and escalate decisions
- "reasoning"    : one sentence explaining why this role + model choice is right for THIS project

Constraints:
- 3 to 8 agents maximum — do NOT pad with unnecessary roles
- Always include PO, Architect, and at least one Dev
- Use Staff seniority only for High complexity projects
- Prefer the cheapest model that can handle the role's cognitive demands
- Do NOT add markdown fences or any text outside the JSON array
"""


def _build_dynamic_catalog(models: list[dict]) -> tuple[str, set[str]]:
    """
    Build a human-readable model catalog string and a set of valid model IDs
    from the live list returned by the proxy.

    Returns (catalog_text, valid_model_ids).
    """
    tier_order = ["strategic", "balanced", "code", "fast", "preview"]
    tier_labels = {
        "strategic": "STRATEGIC — deep reasoning (expensive, use sparingly)",
        "balanced": "BALANCED  — fast reasoning, coordination, reviews",
        "code": "CODE      — specialised code generation (cheapest, use for Dev/QA/DevOps/Data)",
        "fast": "FAST      — lightweight, quick responses",
        "preview": "PREVIEW   — experimental, may be unstable (avoid unless necessary)",
    }

    grouped: dict[str, list[dict]] = {t: [] for t in tier_order}
    for m in models:
        tier = m.get("tier", "balanced")
        if tier not in grouped:
            tier = "balanced"
        grouped[tier].append(m)

    lines = ["Available models (fetched live — only choose IDs listed here):\n"]
    valid_ids: set[str] = set()
    for tier in tier_order:
        entries = grouped[tier]
        if not entries:
            continue
        lines.append(f"{tier_labels[tier]}:")
        for m in sorted(entries, key=lambda x: x["id"]):
            lines.append(f"  - {m['id']:<40} {m['description']}")
            valid_ids.add(m["id"])
        lines.append("")

    return "\n".join(lines), valid_ids
