"""The workspace tools as the model sees them: the OpenAI ``tools`` entries.

Kept apart from ``developer_tools`` (the loop, the record, the wiring) so each
file stays under the repository's size limit. ``SERVER`` and the separator
are the ones the toolbox uses for the names.
"""

from __future__ import annotations

from typing import Any

from services.contracts.tool_calls import WORKSPACE_SERVER
from services.tools import mcp_tools

SERVER = WORKSPACE_SERVER
RUN_TESTS = "run_tests"
EDIT = "edit"
FIND = "find_file"

SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": f"{SERVER}{mcp_tools.TOOL_NAME_SEP}list",
            "description": (
                "List the files and folders of the project under a path "
                "(default: the root), two levels deep unless `depth` says "
                "otherwise; folders end in `/`. Bounded; `truncated` says when."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "A folder, relative to the project root.",
                    },
                    "depth": {
                        "type": "integer",
                        "description": "Levels to descend, 1 to 6 (default 2).",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": f"{SERVER}{mcp_tools.TOOL_NAME_SEP}read",
            "description": (
                "Read a file of the project as it is on disk: a window of "
                "numbered lines from `start_line` (100 by default; `lines` asks "
                "for up to 400, to read a long file in fewer calls), headed "
                "`lines X–Y of N`. Page on with the `start_line` the reply names. "
                "Read a file before you change it."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "The file, relative to the project root.",
                    },
                    "start_line": {
                        "type": "integer",
                        "description": "First line to show (default 1).",
                    },
                    "lines": {
                        "type": "integer",
                        "description": "How many lines to show (default 100, at most 400).",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": f"{SERVER}{mcp_tools.TOOL_NAME_SEP}search",
            "description": (
                "Find the lines matching a regular expression (case-insensitive) "
                "in the project or under a path: `file:line: text`, at most 60 matches. "
                "Past that the reply says how many there are and in which files: "
                "refine the pattern or the path."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "A regular expression.",
                    },
                    "path": {
                        "type": "string",
                        "description": "A folder or file to search in (default: the root).",
                    },
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": f"{SERVER}{mcp_tools.TOOL_NAME_SEP}{FIND}",
            "description": (
                "Find files by name (`greet.py`) or glob (`test_*.py`, "
                "`src/**/models/*.ts`); at most 50 listed, past that the count."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "A file name or a glob; with a `/` it matches the path.",
                    },
                    "path": {
                        "type": "string",
                        "description": "A folder to look in (default: the root).",
                    },
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": f"{SERVER}{mcp_tools.TOOL_NAME_SEP}{RUN_TESTS}",
            "description": (
                "Run the project's tests as they are on disk now (your delivery "
                "is not written yet) and see the output. `command` may narrow "
                "the run within the same runner, e.g. `python -m pytest tests/test_x.py`, "
                "or run a check script the project committed, exactly "
                "`python3 scripts/<name>.py [args]`, at the project root, when "
                "this task did not change it."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "A narrower test command with the project's runner, or `python3 scripts/<name>.py [args]` (optional); any other command is not run, the folder's own test command runs instead.",
                    },
                },
            },
        },
    },
]


def run_tests_schema(
    schema: dict[str, Any], plans: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """*schema* as offered: ``run_tests`` gains ``folder`` when the project's
    suites live in folders (``test_runs``, issue #766); any other unchanged."""
    if not schema["function"]["name"].endswith(RUN_TESTS):
        return schema
    folders = sorted(name for name in plans if name)
    if not folders:
        return schema
    root = "" in plans
    function = dict(schema["function"])
    function["description"] = (
        f"{function['description']} The suites live in folders: "
        + ", ".join(f"`{name}`" for name in folders)
        + (
            "; without `folder` the root's suite runs."
            if root
            else "; name one in `folder`."
        )
        + " The first run of a folder installs its dependencies first."
    )
    parameters = dict(function["parameters"])
    properties = dict(parameters["properties"])
    properties["folder"] = {
        "type": "string",
        "enum": folders,
        "description": "The folder whose suite to run; `command` runs inside it.",
    }
    parameters["properties"] = properties
    if not root:
        parameters["required"] = ["folder"]
    function["parameters"] = parameters
    return {**schema, "function": function}


# Offered only where an edit may touch the files: the task's worktree, or a
# project without the Git workflow (``developer_tools.toolbox_for``).
EDIT_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": f"{SERVER}{mcp_tools.TOOL_NAME_SEP}{EDIT}",
        "description": (
            "Change an existing file now: replace lines `start_line`..`end_line` "
            "with `new_text` (empty deletes them; `end_line` = `start_line` - 1 "
            "inserts), or replace one `search` text that appears exactly once "
            "with `replace`. Applied only if the file still parses (Python, JSON, "
            "JavaScript), else refused with the error and the file unchanged. "
            "Later reads and test runs see the edit, and every edited file is "
            "delivered as it is on disk when you finish: do not repeat it in "
            "your answer. Read the lines before you edit them. It cannot create "
            "a file: write a new file whole in your answer, as a fenced block "
            "whose opening line carries `path=`."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "The file, relative to the project root.",
                },
                "start_line": {
                    "type": "integer",
                    "description": "First line to replace (line mode).",
                },
                "end_line": {
                    "type": "integer",
                    "description": "Last line to replace, inclusive (default start_line).",
                },
                "new_text": {
                    "type": "string",
                    "description": "The lines that take their place (line mode).",
                },
                "search": {
                    "type": "string",
                    "description": "Exact text to find, once (search mode).",
                },
                "replace": {
                    "type": "string",
                    "description": "Its replacement (search mode).",
                },
            },
            "required": ["path"],
        },
    },
}

MOVE = "move"
DELETE = "delete"
# The tools that change the working copy: never read-only.
WRITES = frozenset({EDIT, MOVE, DELETE})
WRITE_NAMES = tuple(f"{SERVER}{mcp_tools.TOOL_NAME_SEP}{w}" for w in sorted(WRITES))

# Beside the edit tool, only when every daemon announces both operations
# (``workspace_edits.can_move``; docs/design/move-and-delete-files.md).
STRUCTURE_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": f"{SERVER}{mcp_tools.TOOL_NAME_SEP}{MOVE}",
            "description": (
                "Move lines `start_line`..`end_line` of `path`, byte for byte, to "
                "`to_path` before its line `to_line` (at the end without it). "
                "`to_path` may be a new file: it is created. Use it to split a "
                "file or move code between files instead of retyping it: the "
                "same bytes arrive, and both files must still parse or nothing "
                "changes. Moved and created files are delivered as they are on "
                "disk when you finish: do not repeat them in your answer."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "The file the lines leave.",
                    },
                    "start_line": {
                        "type": "integer",
                        "description": "First line to move.",
                    },
                    "end_line": {
                        "type": "integer",
                        "description": "Last line to move, inclusive.",
                    },
                    "to_path": {
                        "type": "string",
                        "description": "The file they go to; created when missing.",
                    },
                    "to_line": {
                        "type": "integer",
                        "description": "Insert before this line of `to_path` (default: the end).",
                    },
                },
                "required": ["path", "start_line", "end_line", "to_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": f"{SERVER}{mcp_tools.TOOL_NAME_SEP}{DELETE}",
            "description": (
                "Delete a file once it is empty: move its lines out first. A "
                "file that still has content is refused (a person deletes it). "
                "The deletion is delivered with your other changes."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "The emptied file."},
                },
                "required": ["path"],
            },
        },
    },
]
