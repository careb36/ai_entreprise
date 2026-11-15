"""The sprint board in the task worker: notes from deliveries, notes in prompts (issue #439).

Two jobs, both best-effort (a failure never stops a task):

- ``record_from_delivery`` reads the ``NOTE … END`` blocks of a deliverable
  (``services/contracts/board_notes.parse``) and writes each as a note of the
  sprint with its author (``services/tasks/sprint_board.add``); the blocks
  that were not notes are recorded with their reason. A note the same task
  already left, word for word and still active, is not written twice.
- ``board_section`` is the ``## Sprint board`` prompt section: the sprint's
  active notes relevant to the task (by the files, modules and requirements
  it touches, ``board_notes.relevance``), newest first, within
  ``SPRINT_BOARD_MAX_CHARS``, then how to write one. Superseded and
  marked-wrong notes are left out.

Beside each file a note names, ``with_observations`` puts what the Verifier's
records show (``services/evidence/touched.verified_writers``): the latest
verified run that wrote it, or nothing. It is a field of its own, never the
note's: the note is the author's claim, the observation the platform's.
"""

from __future__ import annotations

import re
from typing import Any

from config import settings
from logging_config import setup_logger
from models import BacklogItem, Sprint
from sqlmodel import Session

from services.contracts import board_notes
from services.contracts.manifest import ALLOWED_ARTIFACT_EXTENSIONS
from services.evidence import touched
from services.loop.worker import related
from services.planning import arch_author, spec_planning
from services.tasks import sprint_board

logger = setup_logger("fabrique-task-worker")

SECTION_NAME = "sprint_board"
# Cut after the memory and the completed tasks (5), with the lessons (4), and
# before the repository and related code (3) and the requirements (2).
PRIORITY = 4
# Room kept for the line that says how many notes were left out.
FOOTER_RESERVE = 80
_PATH = re.compile(r"[\w.\-]+(?:/[\w.\-]+)+|[\w\-]+\.[A-Za-z0-9]{1,8}\b")


def _task_paths(item: BacklogItem) -> list[str]:
    """File-like names in the task's title and description."""
    paths: list[str] = []
    for match in _PATH.finditer(f"{item.title or ''}\n{item.description or ''}"):
        # A sentence's closing period is not part of the name.
        path = match.group(0).removeprefix("./").rstrip(".")
        looks_like_file = "/" in path or any(
            path.lower().endswith(e) for e in ALLOWED_ARTIFACT_EXTENSIONS
        )
        if looks_like_file and path not in paths:
            paths.append(path)
    return paths


def task_scope(
    session: Session, company_id: int, item: BacklogItem
) -> dict[str, list[str]]:
    """What a task touches: ``files`` (named in it, its modules' paths, what
    its earlier runs wrote), ``modules`` (ids and names) and ``requirements``."""
    covers = spec_planning.covers_of(item)
    modules: list[str] = []
    sprint = session.get(Sprint, item.sprint_id)
    if sprint is not None and sprint.specification_id is not None:
        arch = arch_author.approved_for(session, sprint.specification_id)
        if arch is not None:
            content = arch_author.content_of(arch)
            wanted = set(covers) | {c.split(".")[0] for c in covers}
            for module in content.get("modules") or []:
                if isinstance(module, dict) and wanted & set(
                    module.get("covers") or []
                ):
                    modules += [str(module[k]) for k in ("id", "name") if module.get(k)]
    files = _task_paths(item)
    for path in related.seeds_for(session, company_id, item.id):
        if path not in files:
            files.append(path)
    return {"files": files, "modules": modules, "requirements": covers}


def with_observations(
    session: Session, company_id: int | None, notes: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Each note with ``observed``: per file it names, the latest verified run
    that wrote it (``{run_id, backlog_item_id}``) or None. The note's own
    fields are left as they are."""
    files = [f for note in notes for f in (note.get("links") or {}).get("files") or []]
    writers = touched.verified_writers(session, company_id, files) if files else {}
    return [
        note
        | {
            "observed": {
                "files": {
                    f: writers.get(f)
                    for f in (note.get("links") or {}).get("files") or []
                }
            }
        }
        for note in notes
    ]


def _render_note(note: dict[str, Any], why: str) -> str:
    author = note.get("author_role") or "unknown"
    if note.get("author_task_id"):
        author += f", task #{note['author_task_id']}"
    links = note.get("links") or {}
    extra = [
        f"{kind}: {', '.join(links[kind])}"
        for kind in ("symbols", "requirements", "modules")
        if links.get(kind)
    ]
    head = f"- note #{note['id']} · {note['kind']} · claimed by {author}"
    lines = [f"{head} · relevant by {why}: {note['text']}"]
    if extra:
        lines.append("  " + "; ".join(extra))
    for path, seen in ((note.get("observed") or {}).get("files") or {}).items():
        if seen:
            task = seen.get("backlog_item_id")
            lines.append(
                f"  - {path}: observed: changed by verified run #{seen['run_id']}"
                + (f" (task #{task})" if task else "")
            )
        else:
            lines.append(f"  - {path}: not observed")
    return "\n".join(lines) + "\n"


def render(notes: list[tuple[dict[str, Any], str]], max_chars: int) -> str:
    """The section within *max_chars*: the notes that fit, newest first, then
    the format. The header and the format are always there (about 800
    characters); the notes get what is left."""
    if max_chars <= 0:
        return ""
    head = (
        "\n\n## Sprint board\n"
        "Notes the tasks of this sprint left each other. Each is its author's claim, "
        "not a verified fact; beside each file is what the Verifier's records observed. "
        "Do not contradict an active note silently: follow it, or leave a note that "
        "supersedes it and say why.\n\n"
    )
    shown: list[str] = []
    used = 0
    room = max_chars - len(head) - len(board_notes.INSTRUCTIONS) - FOOTER_RESERVE
    for note, why in notes:
        block = _render_note(note, why)
        if used + len(block) > room:
            break
        shown.append(block)
        used += len(block)
    body = "".join(shown) or "No notes concern this task yet.\n"
    left = len(notes) - len(shown)
    if left:
        body += f"({left} more relevant notes on the board were left out for length.)\n"
    return head + body + "\n" + board_notes.INSTRUCTIONS


def relevant_notes(
    notes: list[dict[str, Any]], scope: dict[str, Any]
) -> list[tuple[dict[str, Any], str]]:
    """The active notes that concern *scope*, with why: the ones matched by a
    link first, then the ones for the whole sprint, each newest first."""
    matched: list[tuple[dict[str, Any], str]] = []
    sprint_wide: list[tuple[dict[str, Any], str]] = []
    for note in sorted(notes, key=lambda n: n.get("id") or 0, reverse=True):
        if note.get("status") != board_notes.ACTIVE:
            continue
        why = board_notes.relevance(note, scope)
        if why == "sprint":
            sprint_wide.append((note, why))
        elif why:
            matched.append((note, why))
    return matched + sprint_wide


def board_section(item: dict[str, Any], company_id: int | None, *, engine: Any) -> str:
    """The ``## Sprint board`` section of a task's prompt, or ``""``."""
    max_chars = settings.SPRINT_BOARD_MAX_CHARS
    item_id = item.get("id")
    if max_chars <= 0 or company_id is None or item_id is None or engine is None:
        return ""
    try:
        with Session(engine) as session:
            row = session.get(BacklogItem, item_id)
            if row is None:
                return ""
            notes = [
                sprint_board.as_dict(n)
                for n in sprint_board.notes_of(session, row.sprint_id, active_only=True)
            ]
            chosen = relevant_notes(
                notes, task_scope(session, company_id, row) if notes else {}
            )
            observed = with_observations(session, company_id, [n for n, _ in chosen])
    except Exception as exc:  # noqa: BLE001 - The prompt goes without the board.
        logger.info("No sprint board for item=%s: %s", item_id, exc)
        return ""
    return render(
        [(n, why) for n, (_, why) in zip(observed, chosen, strict=True)], max_chars
    )


def record_from_delivery(
    engine: Any,
    item: dict[str, Any],
    agent: dict[str, Any],
    sprint: dict[str, Any],
    deliverable: str,
    run_id: int | None = None,
) -> dict[str, Any]:
    """Write the notes of *deliverable* on the sprint's board; ``{notes, rejected}``.

    Never raises: the delivery goes on without its notes.
    """
    notes, rejected = board_notes.parse(deliverable)
    if not notes and not rejected:
        return {"notes": [], "rejected": []}
    role = agent.get("role") or item.get("assignee") or "Developer"
    written: list[int] = []
    try:
        with Session(engine) as session:
            row = session.get(BacklogItem, item["id"])
            sprint_row = session.get(Sprint, sprint["id"])
            if row is None or sprint_row is None:
                return {"notes": [], "rejected": rejected}
            own = {
                (n.kind, n.text)
                for n in sprint_board.notes_of(session, sprint_row.id, active_only=True)
                if n.author_task_id == row.id
            }
            for note in notes:
                if (note["kind"], note["text"]) in own:
                    continue
                fields = {
                    "kind": note["kind"],
                    "text": note["text"],
                    "author_role": role,
                    "author_name": agent.get("name") or None,
                    "item": row,
                    "run_id": run_id,
                    "links": note["links"],
                }
                try:
                    added = sprint_board.add(
                        session, sprint_row, supersedes_id=note["supersedes"], **fields
                    )
                except sprint_board.BoardNoteError as exc:
                    rejected.append({"reason": str(exc), "excerpt": note["text"][:120]})
                    if note["supersedes"] is None:
                        continue
                    # The note stands; the link to the note it named does not.
                    added = sprint_board.add(session, sprint_row, **fields)
                own.add((added.kind, added.text))
                written.append(added.id)
            sprint_board.rejected(session, sprint_row, row, role, rejected, run_id)
            session.commit()
    except Exception as exc:  # noqa: BLE001 - Notes never stop a delivery.
        logger.warning("Board notes not recorded for item=%s: %s", item.get("id"), exc)
        return {"notes": [], "rejected": rejected}
    return {"notes": written, "rejected": rejected}
