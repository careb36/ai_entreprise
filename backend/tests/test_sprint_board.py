"""The sprint board's notes (issue #439, ``services/contracts/board_notes.py``):
``NOTE … END`` blocks parsed from a delivery outside its code blocks, with
their kind, links and ``supersedes``; prose that mentions a note is not one,
a malformed block is refused with its reason, at most five per delivery; and
a note's relevance to a task, inactive notes skipped. The notes in the loop
are in ``test_sprint_board_loop.py``, the API in
``test_sprint_board_api.py``.
"""

from __future__ import annotations

import pytest
from services.contracts import board_notes
from services.loop.worker import sprint_notes

# ── The format ───────────────────────────────────────────────────────────────


def test_parses_a_note_with_its_links_outside_the_code_blocks():
    deliverable = (
        "Done.\n\n"
        "```python path=src/store.py\n"
        "NOTE kind=gotcha\nnot a note, inside a file\nEND\n"
        "```\n\n"
        "NOTE kind=decision files=src/store.py,./src/api.py symbols=Store.save "
        "requirements=REQ-1.2 modules=MOD-1\n"
        "Timestamps are stored in UTC;\nconvert at the edges.\n"
        "END\n"
    )
    notes, rejected = board_notes.parse(deliverable)
    assert rejected == []
    assert notes == [
        {
            "kind": "decision",
            "text": "Timestamps are stored in UTC; convert at the edges.",
            "links": {
                "files": ["src/store.py", "src/api.py"],
                "symbols": ["Store.save"],
                "requirements": ["REQ-1.2"],
                "modules": ["MOD-1"],
            },
            "supersedes": None,
        }
    ]


def test_kind_variants_and_supersedes_are_read():
    notes, rejected = board_notes.parse(
        "NOTE kind=interface-change supersedes=#7\nsave() takes a dict\nEND."
    )
    assert rejected == []
    assert notes[0]["kind"] == "interface_change" and notes[0]["supersedes"] == 7


def test_prose_that_mentions_a_note_is_not_one():
    assert board_notes.parse("NOTE: remember the cache.\nEND of story") == ([], [])


@pytest.mark.parametrize(
    ("block", "reason"),
    [
        ("NOTE kind=rumour\nx\nEND", "unknown kind"),
        ("NOTE\nx\nEND", "unknown kind"),
        ("NOTE kind=decision\n\nEND", "no text"),
        ("NOTE kind=decision colour=red\nx\nEND", "unknown attribute"),
        ("NOTE kind=decision supersedes=abc\nx\nEND", "not a note id"),
        ("NOTE kind=decision files=../etc/passwd\nx\nEND", "not a relative path"),
        ("NOTE kind=decision files=/abs.py\nx\nEND", "not a relative path"),
        ("NOTE kind=decision\nno end line", "no END"),
        (f"NOTE kind=decision\n{'x' * (board_notes.TEXT_LIMIT + 1)}\nEND", "over"),
        (
            "NOTE kind=decision files="
            + ",".join(f"f{i}.py" for i in range(board_notes.MAX_LINKS_PER_KIND + 1))
            + "\nx\nEND",
            "more than",
        ),
    ],
)
def test_a_malformed_block_is_refused_with_its_reason(block, reason):
    notes, rejected = board_notes.parse(block)
    assert notes == []
    assert len(rejected) == 1 and reason in rejected[0]["reason"]
    assert rejected[0]["excerpt"].startswith("NOTE")


def test_a_note_without_end_does_not_swallow_the_next_one():
    notes, rejected = board_notes.parse(
        "NOTE kind=gotcha\nlost\nNOTE kind=decision\nkept\nEND"
    )
    assert [n["text"] for n in notes] == ["kept"]
    assert [r["reason"] for r in rejected] == ["no END line"]


def test_at_most_five_notes_per_delivery():
    text = "\n".join(
        f"NOTE kind=decision\nnote {i}\nEND"
        for i in range(board_notes.MAX_NOTES_PER_DELIVERY + 2)
    )
    notes, rejected = board_notes.parse(text)
    assert len(notes) == board_notes.MAX_NOTES_PER_DELIVERY
    assert len(rejected) == 2 and all("more than" in r["reason"] for r in rejected)


# ── Relevance ────────────────────────────────────────────────────────────────


def _note(**links):
    return {"links": {k: links.get(k, []) for k in board_notes.LINK_KINDS}}


@pytest.mark.parametrize(
    ("links", "scope", "why"),
    [
        ({"files": ["src/store.py"]}, {"files": ["src/store.py"]}, "files"),
        ({"files": ["src/store/db.py"]}, {"files": ["src/store/"]}, "files"),
        ({"files": ["src/store"]}, {"files": ["src/store/db.py"]}, "files"),
        ({"files": ["src/storage.py"]}, {"files": ["src/store"]}, None),
        ({"modules": ["mod-2"]}, {"modules": ["MOD-2", "Storage"]}, "modules"),
        ({"modules": ["storage"]}, {"modules": ["MOD-2", "Storage"]}, "modules"),
        ({"requirements": ["REQ-1"]}, {"requirements": ["REQ-1.2"]}, "requirements"),
        ({"requirements": ["REQ-1.3"]}, {"requirements": ["REQ-1.2"]}, "requirements"),
        ({"requirements": ["REQ-2"]}, {"requirements": ["REQ-1.2"]}, None),
        ({}, {"files": ["anything.py"]}, "sprint"),
        ({"symbols": ["Store"]}, {"files": ["src/store.py"]}, None),
    ],
)
def test_relevance_is_the_overlap_of_what_the_note_and_the_task_touch(
    links, scope, why
):
    assert board_notes.relevance(_note(**links), scope) == why


def test_relevant_notes_skip_inactive_and_put_matched_before_sprint_wide():
    notes = [
        {"id": 1, "status": "active", **_note()},
        {"id": 2, "status": "active", **_note(files=["a.py"])},
        {"id": 3, "status": "superseded", **_note(files=["a.py"])},
        {"id": 4, "status": "marked_wrong", **_note()},
        {"id": 5, "status": "active", **_note(files=["b.py"])},
        {"id": 6, "status": "active", **_note(files=["a.py"])},
    ]
    chosen = sprint_notes.relevant_notes(notes, {"files": ["a.py"]})
    assert [(n["id"], why) for n, why in chosen] == [
        (6, "files"),
        (2, "files"),
        (1, "sprint"),
    ]
