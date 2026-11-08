"""The workspace tools as the model sees them: the OpenAI ``tools`` entries.

Kept apart from ``developer_tools`` (the loop, the record, the wiring) so each
file stays under the repository's size limit. ``SERVER`` and the separator
are the ones the toolbox uses for the names.
"""

from __future__ import annotations

from typing import Any

from services import mcp_tools

SERVER = "workspace"
RUN_TESTS = "run_tests"

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
                "Read a file of the project as it is on disk, with line numbers, "
                "at most 400 lines from `start_line`. Read a file before you change it."
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
                "in the project or under a path: `file:line: text`, at most 60 matches."
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
            "name": f"{SERVER}{mcp_tools.TOOL_NAME_SEP}{RUN_TESTS}",
            "description": (
                "Run the project's tests as they are on disk now (your delivery "
                "is not written yet) and see the output. `command` may narrow "
                "the run within the same runner, e.g. `python -m pytest tests/test_x.py`."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "A narrower test command with the project's runner (optional).",
                    },
                },
            },
        },
    },
]
