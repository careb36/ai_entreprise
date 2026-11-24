"""``workspace__read_document``: the text of a DOCX (later PDF) in the project (issue #766).

Documenting a whole project starts from the documents already in it
(``docs/design/agent-toolsets.md``, "Next changes" item 10): a specification
or a manual kept as DOCX or PDF, which ``workspace__read`` shows only as a
binary file. This tool asks the daemon for the document's text instead
(``workspace.read`` with ``extract: "text"``, ``vscode-daemon/document_text.py``,
PROTOCOL.md §5.7), as numbered lines paged with ``start_line``.

- Offered only when every daemon heard announces the ``document_text``
  feature of ``workspace.read`` (``offer``): an older daemon would answer
  with the bytes' "binary file" and the model would learn nothing.
- The path is checked here as ``workspace_git.clean_path`` checks it
  (relative, inside, never VCS metadata or a file that may hold secrets),
  and again by the daemon. Only ``.docx`` and ``.pdf``; the daemon refuses a
  PDF for now (no PDF library among its dependencies) and the model is told.
- What the model sees is bounded by the result budget and its secret-looking
  lines redacted (``context_read.redact_secrets``), as a read's.
- The observation is a read's (``developer_tool_replies.read_fields``): the
  path, the sha256 and size of the file's bytes as the daemon read them from
  disk, never the model's, plus ``format``. So a statement about a document
  can cite the file and digest the platform read, as the pack's ``refs.py``
  cites records.
"""

from __future__ import annotations

from typing import Any

from services.evidence.context_read import redact_secrets
from services.tools import developer_tool_replies, mcp_tools, workspace_git
from services.tools.developer_tool_schemas import SERVER
from services.workspace import daemon_liveness

TOOL = "read_document"
NAME = f"{SERVER}{mcp_tools.TOOL_NAME_SEP}{TOOL}"
SUBJECT = "workspace.read"
FEATURE = "document_text"
SUFFIXES = (".docx", ".pdf")
TIMEOUT_SECONDS = 30.0

SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": NAME,
        "description": (
            "Read the text of a document kept in the project (DOCX; PDF is "
            "not extracted yet), as numbered lines, one per paragraph, "
            "headings marked with #. Page with `start_line`. Nothing is written."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "A .docx or .pdf file, relative to the project root.",
                },
                "start_line": {
                    "type": "integer",
                    "description": "The first line to show (default 1).",
                },
            },
            "required": ["path"],
        },
    },
}


def offer(box: Any) -> None:
    """Set *box*'s ``reads_documents``: every daemon heard announces the feature."""
    if box is not None:
        box.reads_documents = daemon_liveness.announces(SUBJECT, FEATURE) is True


def schemas(box: Any) -> list[dict[str, Any]]:
    return [SCHEMA] if getattr(box, "reads_documents", False) else []


def run(box: Any, arguments: dict[str, Any], extra: dict[str, Any]) -> str:
    """One call: the document's text the model sees; *extra* gets the observation."""
    path = workspace_git.clean_path(arguments.get("path"))
    if not path.lower().endswith(SUFFIXES):
        raise ValueError(f"not a document this tool reads (.docx, .pdf): {path}")
    reply = box.requester(
        SUBJECT,
        {
            "workspace_root": box.workspace_root,
            "path": path,
            "start_line": arguments.get("start_line"),
            "lines": developer_tool_replies.READ_ASK_LINES,
            "extract": "text",
        },
        TIMEOUT_SECONDS,
    )
    if not reply.get("extracted"):
        # An older daemon read the bytes: never shown as the document's text.
        raise RuntimeError("the workspace daemon did not extract the document's text")
    reply = {**reply, "text": redact_secrets(path, str(reply.get("text") or ""))}
    text = developer_tool_replies.read_text(path, reply, box.result_max_chars)
    extra.update(developer_tool_replies.read_fields(reply, text))
    extra["format"] = reply.get("format")
    return text
