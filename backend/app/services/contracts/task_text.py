"""How long a task's description may be, wherever a task is made or edited.

A task's description is what its agent is asked to do; cutting it cuts the
instruction. Until 2026-11-22 the planners cut every description at 500
characters while a Manager could write 10 000: QA's acceptance-test template
with its criteria names ran past 500, so the sentence "do not implement the
feature here" was lost and the agent read "The tests fail u" (bench, spec 05,
issue #774). One limit now holds for every path: the planners, the test-task
templates and a Manager's edit (``services/loop/task_edit.py``).
"""

from __future__ import annotations

DESCRIPTION_MAX = 10_000


def clip_description(text: object) -> str:
    """*text* as a task description: a string, at most ``DESCRIPTION_MAX``."""
    return str(text or "")[:DESCRIPTION_MAX]
