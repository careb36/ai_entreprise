"""The repository map in a prompt: the top of the tree and the ranked definitions.

The daemon answers ``workspace.map`` (``vscode-daemon/repo_map.py``) with the
project's classes, functions and methods, their signatures and line numbers,
ranked by how many other files refer to them and cut to a budget. The idea
is Aider's repository map (Apache-2.0; no code taken): tree-sitter and
PageRank there, the standard library's ``ast``, a conservative extractor for
JavaScript and TypeScript and a deterministic PageRank on the daemon here.

The section goes into the Developer's and QA's task prompts (with a word on
the workspace tools), the Architect's consultation brief and the Architect's
draft of an architecture, beside the top two levels of the tree, each within
its budget (``REPO_MAP_MAX_CHARS`` for the definitions; ``0`` leaves them
out). It is context, never evidence: the reads the tools make are the
observations.

The daemon redacts secret-looking literals in the signatures it maps; the
map is redacted again here, line by line, as the read replies are
(``context_read.redact_secrets``), so an older daemon's map or a pattern only
the backend knows never reaches a prompt with the value.

A daemon that does not answer ``workspace.map`` (an older one) leaves the
tree alone in the section; one that cannot list the project leaves the
section out.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from config import settings

from services.context_read import redact_secrets

logger = logging.getLogger("fabrique-task-worker")

LIST_TIMEOUT_SECONDS = 20.0
MAP_TIMEOUT_SECONDS = 40.0
TREE_MAX_CHARS = 2500
ARCHITECT_ROLES = {"architect"}

Requester = Callable[[str, dict[str, Any], float], dict[str, Any]]

_TOOLS_NOTE = (
    "Use the workspace tools to read the files you will change and the ones "
    "around them before you write, and to search for what you need; a file you "
    "deliver replaces the one on disk whole, so deliver it complete."
)
_CONTEXT_NOTE = (
    "Keep what you propose to what is there: name the files and definitions "
    "a change touches."
)
_MAP_NOTE = (
    "The project's classes and functions with their signatures and line "
    "numbers, ranked by how many other files use them (a repository map, after "
    "Aider's). It says where things are, not what they do: read a file before "
    "you rely on it."
)


def tree_text(reply: dict[str, Any], max_chars: int = TREE_MAX_CHARS) -> str:
    """The listing, cut at *max_chars* on a whole line."""
    entries = [str(e) for e in reply.get("entries") or []]
    if not entries:
        return ""
    listing = "\n".join(entries)
    if len(listing) > max_chars:
        return listing[:max_chars].rsplit("\n", 1)[0] + "\n[listing cut here]"
    if reply.get("truncated"):
        listing += "\n[listing cut here]"
    return listing


def map_text(reply: dict[str, Any] | None) -> str:
    """The daemon's map, saying how many definitions it left out."""
    if not isinstance(reply, dict):
        return ""
    text = redact_secrets("repository map", str(reply.get("map") or "")).strip()
    if not text:
        return ""
    total, shown = reply.get("definitions"), reply.get("shown")
    if reply.get("truncated") and isinstance(total, int) and isinstance(shown, int):
        text += f"\n[{shown} of {total} definitions shown; search for the rest]"
    if reply.get("partial"):
        text += "\n[the project is larger than the map reads; some files are not in it]"
    return text


def compose(listing: str, definitions: str, *, tools: bool) -> str:
    """The prompt section, or ``""`` without a listing."""
    if not listing:
        return ""
    section = (
        "\n\n## Repository\n"
        "The project exists; the tree below is its top two levels as they are on "
        f"disk now. {_TOOLS_NOTE if tools else _CONTEXT_NOTE}\n\n{listing}"
    )
    if definitions:
        section += f"\n\n### Definitions, most used first\n{_MAP_NOTE}\n\n{definitions}"
    return section


def map_request(root: str) -> dict[str, Any]:
    return {"workspace_root": root, "max_chars": settings.REPO_MAP_MAX_CHARS}


def section(
    requester: Requester,
    root: str,
    *,
    tools: bool,
    tree_chars: int = TREE_MAX_CHARS,
) -> str:
    """The section for *root*, from a synchronous daemon *requester*."""
    try:
        listing = tree_text(
            requester(
                "workspace.list",
                {"workspace_root": root, "path": None, "depth": 2},
                LIST_TIMEOUT_SECONDS,
            ),
            tree_chars,
        )
    except Exception as exc:  # noqa: BLE001 - The prompt goes without the map.
        logger.warning("Could not list the project %s: %s", root, exc)
        return ""
    definitions = ""
    if listing and settings.REPO_MAP_MAX_CHARS > 0:
        try:
            definitions = map_text(
                requester("workspace.map", map_request(root), MAP_TIMEOUT_SECONDS)
            )
        except Exception as exc:  # noqa: BLE001 - The tree alone, as before.
            logger.info("No repository map for %s: %s", root, exc)
    return compose(listing, definitions, tools=tools)


async def fetch_section(nats_client: Any, root: str | None) -> str:
    """The same section from the backend's own NATS client (the Architect's
    draft, outside the task worker); ``""`` without a daemon or a folder."""
    if not root or nats_client is None or settings.REPO_MAP_MAX_CHARS <= 0:
        return ""
    from services.workspace_daemon import daemon_request

    try:
        listing = tree_text(
            await daemon_request(
                nats_client,
                "workspace.list",
                {"workspace_root": root, "path": None, "depth": 2},
                LIST_TIMEOUT_SECONDS,
            )
        )
    except Exception as exc:  # noqa: BLE001 - The draft goes without the map.
        logger.info("Could not list the project %s: %s", root, exc)
        return ""
    try:
        definitions = map_text(
            await daemon_request(
                nats_client, "workspace.map", map_request(root), MAP_TIMEOUT_SECONDS
            )
        )
    except Exception as exc:  # noqa: BLE001 - The tree alone.
        logger.info("No repository map for %s: %s", root, exc)
        definitions = ""
    return compose(listing, definitions, tools=False)
