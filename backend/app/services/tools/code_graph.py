"""The related code in the Developer's prompt (issue #438, first slice).

From a task's seeds (the paths of the approved-architecture modules its
requirements map to, and the files its earlier runs wrote;
``services/loop/worker/related.py``) the daemon's ``workspace.related``
(``vscode-daemon/code_graph.py``) walks the project's reference graph, the
repository map's, computed on demand from its parse cache: which files the
seeds use and which use them, with the names that tie them. The section names
the files a change could break, and the recent runs the daemon reports wrote
any of these files, with the verdict the Verifier recorded
(``services/evidence/touched.py``), refuted ones marked.

It is context, never evidence: the graph is a parse of the files as they are
now (a name used is not a call proven), and the runs are past observations
about other deliveries. The section is cut at ``CODE_GRAPH_MAX_CHARS`` (``0``
leaves it out). The wait is bounded (``TIMEOUT_SECONDS``); no seeds, a daemon
without the subject (an older one), a timeout, an error or an empty reply
leave the section out (``""``), and the prompt is what it was before.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from config import settings
from sqlmodel import Session

from services.contracts.verdicts import VERDICT_REFUTED
from services.evidence import touched
from services.evidence.context_read import redact_secrets

logger = logging.getLogger("fabrique-task-worker")

SUBJECT = "workspace.related"
TIMEOUT_SECONDS = 10.0
MAX_SEEDS = 50
HOPS = 1
MAX_FILES = 30
RUNS_SHOWN = 5
NAMES_SHOWN = 8

Requester = Callable[[str, dict[str, Any], float], dict[str, Any]]

_NOTE = (
    "Context from the workspace daemon's parse of the project as it is on disk "
    "now, not evidence: the files this task starts from, the files they use and "
    "the files that use them, each with the names that tie them. A change to a "
    "starting file can break the files that use it; read them before you change "
    "a name they use."
)
_RUNS_NOTE = (
    "What the daemon reported earlier deliveries wrote, with the verdict the "
    "Verifier recorded for each run."
)
_CUT = "\n[related code cut here; search for the rest]"


def clean_seeds(seeds: list[Any]) -> list[str]:
    """The seeds as unique, non-empty strings, in order, at most ``MAX_SEEDS``."""
    out: list[str] = []
    for seed in seeds or []:
        text = seed.strip() if isinstance(seed, str) else ""
        if text and text not in out:
            out.append(text)
    return out[:MAX_SEEDS]


def _merge(
    rows: list[dict[str, Any]], key: str, skip: set[str]
) -> dict[str, list[str]]:
    """The edges *key* of *rows*, merged by path, less the files in *skip*."""
    merged: dict[str, list[str]] = {}
    for row in rows:
        for edge in row.get(key) or []:
            if not isinstance(edge, dict) or not isinstance(edge.get("path"), str):
                continue
            path = edge["path"]
            if path in skip:
                continue
            names = merged.setdefault(path, [])
            for name in edge.get("names") or []:
                if isinstance(name, str) and name not in names:
                    names.append(name)
    return merged


def _lines(merged: dict[str, list[str]]) -> list[str]:
    rows = sorted(merged.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    return [
        f"- {path}: {', '.join(names[:NAMES_SHOWN])}" if names else f"- {path}"
        for path, names in rows
    ]


def _run_line(run: dict[str, Any]) -> str:
    verdict = str(run.get("verdict") or "no verdict")
    mark = " — REFUTED: its delivery did not hold" if verdict == VERDICT_REFUTED else ""
    title = str(run.get("title") or f"task {run.get('backlog_item_id')}")
    return f'- "{title}" ({verdict}{mark}): {", ".join(run.get("written") or [])}'


def render(reply: dict[str, Any], runs: list[dict[str, Any]], max_chars: int) -> str:
    """The section from the daemon's *reply* and the *runs*; ``""`` without files."""
    files = [f for f in reply.get("files") or [] if isinstance(f, dict)]
    seeds = [f for f in files if f.get("seed") and isinstance(f.get("path"), str)]
    if not seeds or max_chars <= 0:
        return ""
    seed_paths = {f["path"] for f in seeds}
    parts = [
        "\n\n## Related code\n" + _NOTE,
        "### Starting files\n" + "\n".join(f"- {p}" for p in sorted(seed_paths)),
    ]
    uses = _lines(_merge(seeds, "uses", seed_paths))
    if uses:
        parts.append("### What they use\n" + "\n".join(uses))
    used_by = _lines(_merge(seeds, "used_by", seed_paths))
    if used_by:
        parts.append(
            "### What uses them (a change could break these)\n" + "\n".join(used_by)
        )
    if runs:
        parts.append(
            "### Recent runs that touched these files\n"
            + _RUNS_NOTE
            + "\n"
            + "\n".join(_run_line(r) for r in runs)
        )
    if reply.get("partial"):
        parts.append(
            "[the project is larger than the daemon walked; some files are not in it]"
        )
    text = redact_secrets("related code", "\n\n".join(parts))
    if len(text) > max_chars:
        text = text[: max(0, max_chars - len(_CUT))].rsplit("\n", 1)[0] + _CUT
    return text


def related_section(
    request_fn: Requester,
    root: str | None,
    seeds: list[Any],
    *,
    company_id: int | None,
    engine: Any,
) -> str:
    """The ``## Related code`` section for *seeds* in *root*, or ``""``."""
    max_chars = settings.CODE_GRAPH_MAX_CHARS
    seeds = clean_seeds(seeds)
    if max_chars <= 0 or not root or not seeds:
        return ""
    try:
        reply = request_fn(
            SUBJECT,
            {
                "workspace_root": root,
                "seeds": seeds,
                "hops": HOPS,
                "max_files": MAX_FILES,
            },
            TIMEOUT_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001 - The prompt goes without the section.
        logger.info("No related code for %s: %s", root, exc)
        return ""
    if not isinstance(reply, dict) or not isinstance(reply.get("files"), list):
        return ""
    paths = [
        f["path"]
        for f in reply["files"]
        if isinstance(f, dict) and isinstance(f.get("path"), str)
    ]
    runs: list[dict[str, Any]] = []
    if paths and engine is not None:
        try:
            with Session(engine) as session:
                runs = touched.runs_touching(session, company_id, paths, RUNS_SHOWN)
        except Exception as exc:  # noqa: BLE001 - The graph alone.
            logger.info("Could not read the runs that touched %s: %s", root, exc)
    return render(reply, runs, max_chars)
