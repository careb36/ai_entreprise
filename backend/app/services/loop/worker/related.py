"""The seeds of a task's related code, and the section they give (issue #438).

A task starts from two kinds of files: the ``path`` of every module of the
approved architecture that covers one of its requirements (a file or a
folder; ``services/planning/arch_author``), and, on rework, the files the
daemon reports its own earlier runs wrote (``services/evidence/touched``).
``services/tools/code_graph.related_section`` asks the daemon for the files
around them and renders the ``## Related code`` section; with no seeds the
section is empty and the prompt is unchanged.

Before the section, ``code_graph.persist`` brings the company's stored code
graph up to date from the daemon (only the files whose sha256 changed) and
records the edges the approved architecture states; the section then reads
the stored graph, or asks the daemon on demand when nothing is stored.
"""

from __future__ import annotations

from typing import Any

from config import settings
from logging_config import setup_logger
from models.sprint import BacklogItem, Sprint
from sqlmodel import Session

from services.evidence import touched
from services.planning import arch_author, spec_planning
from services.tools import code_graph, developer_tools

logger = setup_logger("fabrique-task-worker")


def module_paths(arch: dict[str, Any], covers: list[str]) -> list[str]:
    """The paths of the modules covering any of *covers* (criteria ids, or
    the requirement ids they belong to: ``R1.2`` names ``R1`` too)."""
    wanted = set(covers) | {c.split(".")[0] for c in covers}
    paths: list[str] = []
    for module in arch.get("modules") or []:
        if not isinstance(module, dict) or not wanted & set(module.get("covers") or []):
            continue
        path = module.get("path")
        if isinstance(path, str) and path.strip() and path.strip() not in paths:
            paths.append(path.strip())
    return paths


def architecture_for(
    session: Session, item: BacklogItem
) -> tuple[dict[str, Any] | None, int | None]:
    """The approved architecture that holds for the task's sprint, as its
    specification sees it, and its id; ``(None, None)`` without one."""
    sprint = session.get(Sprint, item.sprint_id)
    if sprint is None or sprint.specification_id is None:
        return None, None
    arch = arch_author.approved_for(session, sprint.specification_id)
    if arch is None:
        return None, None
    return arch_author.content_for(session, sprint.specification_id), arch.id


def seeds_for(session: Session, company_id: int, item_id: int) -> list[str]:
    """The task's modules' paths, then the files its earlier runs wrote."""
    item = session.get(BacklogItem, item_id)
    if item is None:
        return []
    seeds: list[str] = []
    content, _arch_id = architecture_for(session, item)
    if content is not None:
        seeds += module_paths(content, spec_planning.covers_of(item))
    for path in touched.written_by_task(session, company_id, item_id):
        if path not in seeds:
            seeds.append(path)
    return seeds[: code_graph.MAX_SEEDS]


def related_code_section(
    item: dict[str, Any], agent: dict[str, Any], company_id: int | None, *, engine: Any
) -> str:
    """The ``## Related code`` section of a Developer's task, or ``""``: for a
    role with the workspace tools, a daemon reachable and a task with seeds."""
    item_id = item.get("id")
    if company_id is None or item_id is None or engine is None:
        return ""
    if settings.CODE_GRAPH_MAX_CHARS <= 0 or not developer_tools.offered_to(
        agent.get("role")
    ):
        return ""
    if "nats" not in developer_tools._bridge:
        return ""
    architecture: dict[str, Any] | None = None
    architecture_id: int | None = None
    try:
        with Session(engine) as session:
            seeds = seeds_for(session, company_id, item_id)
            item_row = session.get(BacklogItem, item_id) if seeds else None
            if item_row is not None:
                architecture, architecture_id = architecture_for(session, item_row)
    except Exception as exc:  # noqa: BLE001 - The prompt goes without the section.
        logger.info("No seeds for the related code of item=%s: %s", item_id, exc)
        return ""
    if not seeds:
        return ""
    root = developer_tools.tool_root(company_id, item_id, engine=engine)
    code_graph.persist(
        developer_tools._request_from_thread,
        engine,
        company_id,
        root,
        architecture=architecture,
        architecture_id=architecture_id,
    )
    return code_graph.related_section(
        developer_tools._request_from_thread,
        root,
        seeds,
        company_id=company_id,
        engine=engine,
    )
