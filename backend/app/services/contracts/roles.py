"""The role catalogue: the roles a company can hire, and what each one does in the loop.

Roles are stored as free text on ``Agent.role`` and ``BacklogItem.assignee``;
this module is the one place that says which names the factory knows and
which of them take the Developer's path. The names are the ``AgentRole``
values of ``services/models/model_factory.py`` (``tests/test_roles.py`` keeps the two in
step): CamelCase, no spaces, as ``ScrumMaster`` and ``TechWriter`` already
were. ``Developer`` keeps working for existing companies; the specialised
coding roles (``BackendDeveloper``, ``FrontendDeveloper``, ``DevOps``,
``DataEngineer``) deliver files, get the Architect's consultation, the edit
plan, the workspace tools and every Developer standard exactly as a
``Developer`` does. ``Designer`` writes UI specifications and markup: it gets
the workspace tools (it reads the existing components before it specifies
one) and the research tools, and the UX standard.

The older spellings the frontend and the discovery wizard store (``Dev``,
``BackendDev``, ``SM``, ``Security``, ``Data``…) are aliases read through
``canonical``; nothing stored is rewritten.
"""

from __future__ import annotations

from services.contracts import tool_steps

PO, ARCHITECT, SCRUM_MASTER, DEVELOPER, QA, SECOPS, TECH_WRITER = (
    "PO",
    "Architect",
    "ScrumMaster",
    "Developer",
    "QA",
    "SecOps",
    "TechWriter",
)
BACKEND_DEVELOPER, FRONTEND_DEVELOPER, DEVOPS, DESIGNER, DATA_ENGINEER = (
    "BackendDeveloper",
    "FrontendDeveloper",
    "DevOps",
    "Designer",
    "DataEngineer",
)

CORE_ROLES: tuple[str, ...] = (
    PO,
    ARCHITECT,
    SCRUM_MASTER,
    DEVELOPER,
    QA,
    SECOPS,
    TECH_WRITER,
)
NEW_ROLES: tuple[str, ...] = (
    BACKEND_DEVELOPER,
    FRONTEND_DEVELOPER,
    DEVOPS,
    DESIGNER,
    DATA_ENGINEER,
)
ROLES: tuple[str, ...] = CORE_ROLES + NEW_ROLES

# The roles that deliver code the way Developer does.
DEVELOPER_ROLES: tuple[str, ...] = (
    DEVELOPER,
    BACKEND_DEVELOPER,
    FRONTEND_DEVELOPER,
    DEVOPS,
    DATA_ENGINEER,
)
# The roles given the workspace tools before the step set the ceiling
# (``tool_steps``, 2026-11-24): kept as the catalogue's coding-and-design group.
FILE_ROLES: tuple[str, ...] = (*DEVELOPER_ROLES, QA, DESIGNER)
# The roles that research before deciding (the ``RESEARCH_ROLES`` default).
RESEARCH_ROLES: tuple[str, ...] = (ARCHITECT, PO, SECOPS, TECH_WRITER, DESIGNER)

# Stored spellings that are not catalogue names, by their normalised form.
ALIASES: dict[str, str] = {
    "dev": DEVELOPER,
    "engineer": DEVELOPER,
    "softwareengineer": DEVELOPER,
    "fullstackdev": DEVELOPER,
    "mobiledev": DEVELOPER,
    "mlengineer": DEVELOPER,
    "backenddev": BACKEND_DEVELOPER,
    "frontenddev": FRONTEND_DEVELOPER,
    "data": DATA_ENGINEER,
    "sre": DEVOPS,
    "uxuidesigner": DESIGNER,
    "productowner": PO,
    "sm": SCRUM_MASTER,
    "security": SECOPS,
    "tester": QA,
    "quality": QA,
    "qualityassurance": QA,
}


def normalise(role: str | None) -> str:
    """*role* lowercased, without spaces, dashes, underscores or slashes."""
    return "".join(ch for ch in (role or "").lower() if ch not in " -_/")


_BY_KEY = {normalise(r): r for r in ROLES}


def canonical(role: str | None) -> str:
    """The catalogue name a stored role stands for; anything else stripped as is."""
    key = normalise(role)
    return _BY_KEY.get(key) or ALIASES.get(key) or (role or "").strip()


def is_developer(role: str | None) -> bool:
    """Whether *role* takes the Developer's path (delivery, Architect, edit plan)."""
    return canonical(role) in DEVELOPER_ROLES


def delivers_files(role: str | None) -> bool:
    """Whether *role*, executing a task, gets the workspace tools to read and
    run (``tool_steps``: the execute step's ceiling, narrowed by the role)."""
    allowed = tool_steps.families(tool_steps.EXECUTE, canonical(role))
    return tool_steps.READ in allowed and tool_steps.RUN in allowed
