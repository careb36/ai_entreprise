"""The edit format per model, and the format a delivery was observed in (issue #689).

The format a model writes file changes in changes how often it succeeds
(#359, A.8; the method is Aider's edit-format benchmark, no code taken). The
factory accepts three shapes today: whole files in ``path=`` blocks,
SEARCH/REPLACE edits in a block marked ``edit`` (``contracts/edit_blocks``,
#361) and the ``workspace__edit`` tool during the tool loop. This module
makes the instructions a per-model setting the bench can vary:

- ``auto`` (the default, unchanged): whole files, with SEARCH/REPLACE edits
  allowed for an existing file;
- ``whole``: whole files only;
- ``search_replace``: SEARCH/REPLACE edits for every existing file, whole
  files only for new ones.

``EDIT_FORMAT_BY_MODEL`` (JSON, ``{"<model>": "<format>", "*": "<format>"}``)
maps the model the task worker requests to a format; a model it does not
name takes ``*``, else ``auto``. A value that cannot be read is dropped with
a warning, so a typo never changes a format silently.

``observe`` is the backend's own reading of what came back: the file blocks
of the answer (parsed by the backend, not described by the model) and the
tool calls the backend executed. What the model says about its format is a
claim and is never read. It goes on the run's evidence as ``edit_format``
beside the requested format, so a format the model could not use (malformed
edit blocks, blocks in a format it was not asked for) is recorded as such.
Pure: no I/O.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from services.contracts.tool_calls import WORKSPACE_SERVER

logger = logging.getLogger("fabrique-task-worker")

VERSION = 1
AUTO = "auto"
WHOLE = "whole"
SEARCH_REPLACE = "search_replace"
FORMATS = (AUTO, WHOLE, SEARCH_REPLACE)
ANY_MODEL = "*"
EDIT_TOOL = "edit"


def parse_setting(text: str | None) -> dict[str, str]:
    """The model → format map in *text*; entries that cannot be read dropped."""
    if not str(text or "").strip():
        return {}
    try:
        data = json.loads(str(text))
    except ValueError:
        logger.warning("EDIT_FORMAT_BY_MODEL is not JSON; every model runs on auto")
        return {}
    if not isinstance(data, dict):
        logger.warning(
            "EDIT_FORMAT_BY_MODEL is not an object; every model runs on auto"
        )
        return {}
    out: dict[str, str] = {}
    for model, value in data.items():
        fmt = str(value or "").strip().lower()
        if not str(model).strip() or fmt not in FORMATS:
            logger.warning(
                "EDIT_FORMAT_BY_MODEL: %r → %r dropped (formats: %s)",
                model,
                value,
                ", ".join(FORMATS),
            )
            continue
        out[str(model).strip()] = fmt
    return out


def format_for(model: str | None, setting: str | None) -> dict[str, Any]:
    """The format for *model* under *setting*, and where it came from:
    ``model`` (named), ``any_model`` (``*``) or ``default`` (``auto``)."""
    table = parse_setting(setting)
    if model and model in table:
        return {"format": table[model], "source": "model", "model": model}
    if ANY_MODEL in table:
        return {"format": table[ANY_MODEL], "source": "any_model", "model": model}
    return {"format": AUTO, "source": "default", "model": model}


def _is_edit_call(call: Any) -> bool:
    return (
        isinstance(call, dict)
        and call.get("server") == WORKSPACE_SERVER
        and call.get("tool") == EDIT_TOOL
    )


def observe(
    entries: list[dict[str, Any]], tool_calls: Any, requested: dict[str, Any] | None
) -> dict[str, Any]:
    """What the backend saw of the answer's format.

    *entries* is the answer's file manifest (``contracts.manifest``): an
    entry with ``content`` is a whole file, one with ``edits`` an edit block
    (an empty list when the block held no well-formed edit). *tool_calls*
    are the tool loop's observations. ``off_format`` counts edit blocks
    under ``whole``. ``well_formed`` is False when any edit block was
    malformed or off the format asked for; the daemon's own refusals
    (``workspace_writes`` with ``rejected_edit``) are read beside it.
    """
    fmt = (requested or {}).get("format") or AUTO
    whole = sum(1 for e in entries if "edits" not in e)
    blocks = [e for e in entries if "edits" in e]
    malformed = sum(1 for e in blocks if not e.get("edits"))
    edit_calls = [c for c in tool_calls or [] if _is_edit_call(c)]
    tool_errors = sum(1 for c in edit_calls if c.get("is_error"))
    off_format = len(blocks) if fmt == WHOLE else 0
    used = [
        name
        for name, count in (
            (WHOLE, whole),
            (SEARCH_REPLACE, len(blocks) - malformed),
            ("workspace_edit", len(edit_calls) - tool_errors),
        )
        if count
    ]
    return {
        "version": VERSION,
        "requested": fmt,
        "source": (requested or {}).get("source") or "default",
        "whole_files": whole,
        "edit_blocks": len(blocks) - malformed,
        "malformed_edit_blocks": malformed,
        "off_format": off_format,
        "tool_edits": len(edit_calls) - tool_errors,
        "tool_edit_errors": tool_errors,
        "used": used,
        "well_formed": malformed == 0 and off_format == 0,
    }
