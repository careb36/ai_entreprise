"""SEARCH/REPLACE edits in a deliverable (the format Aider uses, Apache-2.0).

A file block whose opening line carries ``edit`` after its path holds edits
instead of the whole file::

    ```python path=src/app.py edit
    <<<<<<< SEARCH
    def greet():
        return "hi"
    =======
    def greet(name):
        return f"hi {name}"
    >>>>>>> REPLACE
    ```

A block whose body is nothing but well-formed edits is read as edits even
without the flag (Aider reads its blocks the same way): a whole file never
consists of edits alone, and written as one it would not parse.

The daemon applies them to the file on disk (``vscode-daemon/file_edits.py``):
each search text must appear exactly once, and the result must still parse.
A block that holds no well-formed edit, or text outside its edits (a block
left unclosed, say), yields an empty list: the daemon refuses it with the
reason, so the agent hears why in its rework request and nothing is half
applied.

The backend never sees the edited file, only the edits. ``scanned_text``
gives the checks that read a delivery (secrets, TODOs, dependencies) the text
an edit adds, never the text it removes.
"""

from __future__ import annotations

import re

_EDIT_FLAG = re.compile(r"(?:^|\s)edit(?:\s|$)")
_EDIT = re.compile(
    r"^<{5,9} SEARCH[ \t]*\n(?P<search>.*?)^={5,9}[ \t]*\n(?P<replace>.*?)^>{5,9} REPLACE[ \t]*$",
    flags=re.DOTALL | re.MULTILINE,
)

INSTRUCTIONS = (
    "\n\nTo change part of an existing file, you may send only the changes: add `edit` "
    "after the path and write one or more blocks of the form\n"
    "<<<<<<< SEARCH\n<exact lines from the current file>\n=======\n<their replacement>\n"
    ">>>>>>> REPLACE\n"
    "Each SEARCH text must match the current file exactly once (include enough "
    "surrounding lines), and the edited file must still parse. Use whole files for "
    "new files."
)


def is_edit_block(info: str, path_match: re.Match) -> bool:
    """Whether the opening line marks the block as edits (``edit`` outside the path)."""
    rest = info[: path_match.start()] + " " + info[path_match.end() :]
    return bool(_EDIT_FLAG.search(rest))


def parse(body: str) -> list[dict[str, str]]:
    """The edits in *body*; ``[]`` when any text lies outside them."""
    matches = list(_EDIT.finditer(body or ""))
    if _EDIT.sub("", body or "").strip():
        return []
    return [
        {"search": m.group("search"), "replace": m.group("replace")} for m in matches
    ]


def scanned_text(entry: dict) -> str:
    """The text of a manifest entry a check should read: the whole file, or
    for edits only their replacement texts (what the delivery adds)."""
    if "edits" in entry:
        return "\n".join(str(e.get("replace") or "") for e in entry["edits"])
    return str(entry.get("content") or "")


_VERSIONED_PAIR = re.compile(
    r'"(@?[A-Za-z0-9][\w.\-/]*)"\s*:\s*"((?:[\^~<>=*]|\d|latest|workspace:|npm:)[^"]*)"'
)


def json_dependency_pairs(text: str) -> dict[str, str]:
    """``"name": "version"`` pairs in a fragment of ``package.json`` that does
    not parse on its own (an edit's replacement); values that do not look
    like a version range (scripts, fields) are left out."""
    return {m.group(1): m.group(2) for m in _VERSIONED_PAIR.finditer(text or "")}
