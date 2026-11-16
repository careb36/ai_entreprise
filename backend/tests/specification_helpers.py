"""What the tests of the specification share: a company, a well-formed
specification (``GOOD``) and its criterion. The tests are
``test_specifications*.py``; other test files import ``GOOD`` from
``test_specifications``, which re-exports it.
"""

import secrets

from models import Company


def _company(session):
    company = Company(company_name=f"Spec {secrets.token_hex(3)}", autonomy_level=2)
    session.add(company)
    session.commit()
    return company


CRITERION = {
    "given": "a signed-in user",
    "when": "they open the board",
    "then": "their tasks are listed",
}


GOOD = {
    "title": "Task board",
    "summary": "A board where a team sees its tasks.",
    "requirements": [
        {
            "title": "List the tasks",
            "description": "The board lists the tasks of the signed-in user's company.",
            "criteria": [
                CRITERION,
                {**CRITERION, "then": "the list has one row per task"},
            ],
        },
        {
            "title": "Move a task",
            "description": "A task is dragged between columns and keeps its new status.",
            "criteria": [
                {
                    "given": "a task in todo",
                    "when": "it is dropped on done",
                    "then": "its status reads done",
                }
            ],
        },
    ],
    "quality": [
        {"attribute": "performance", "target": "the board renders in under 300 ms"}
    ],
    "constraints": ["TypeScript and React"],
}
