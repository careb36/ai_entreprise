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

The daemon applies them to the file on disk (``vscode-daemon/file_edits.py``):
each search text must appear exactly once, and the result must still parse.
A block that holds no well-formed edit yields an empty list, which the daemon
refuses with the reason, so the agent hears why in its rework request.
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
    return [
        {"search": m.group("search"), "replace": m.group("replace")}
        for m in _EDIT.finditer(body or "")
    ]
