"""The OpenAI-style tool schemas of the research tools
(``services/research_tools.py``): what the model is offered."""

from __future__ import annotations

from typing import Any

from services import mcp_tools
from services.contracts.tool_calls import (
    RESEARCH_ADVISORIES,
    RESEARCH_FETCH,
    RESEARCH_PACKAGE,
    RESEARCH_SERVER,
)
from services.research_registries import ECOSYSTEMS

SERVER = RESEARCH_SERVER
FETCH, PACKAGE, ADVISORIES = RESEARCH_FETCH, RESEARCH_PACKAGE, RESEARCH_ADVISORIES

SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": f"{SERVER}{mcp_tools.TOOL_NAME_SEP}{FETCH}",
            "description": (
                "Read one page of official documentation, release notes or a "
                "standard, as text, from a host the company allows. Check a "
                "version, an API or a security fact at its source before you "
                "assert it; your own knowledge has a cut-off date."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "The page's https URL."}
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": f"{SERVER}{mcp_tools.TOOL_NAME_SEP}{PACKAGE}",
            "description": (
                "A package's record at its registry: the latest version and "
                "when it was released, the last versions, the summary. Look a "
                "dependency up before you choose or pin it."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "The package name as the registry knows it.",
                    },
                    "ecosystem": {
                        "type": "string",
                        "enum": list(ECOSYSTEMS),
                        "description": "pypi, npm, crates or go.",
                    },
                },
                "required": ["name", "ecosystem"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": f"{SERVER}{mcp_tools.TOOL_NAME_SEP}{ADVISORIES}",
            "description": (
                "The known vulnerabilities of a package, and of one version "
                "of it when given, from the OSV database."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "ecosystem": {"type": "string", "enum": list(ECOSYSTEMS)},
                    "version": {
                        "type": "string",
                        "description": "The version in use (optional).",
                    },
                },
                "required": ["name", "ecosystem"],
            },
        },
    },
]
