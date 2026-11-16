"""What the tests of the architecture share: a well-formed architecture
(``GOOD_ARCH``), a company with an approved specification, and a fake daemon
that writes files. The tests are ``test_architectures*.py``; other test files
import these names from ``test_architectures``, which re-exports them.
"""

import secrets

from models import Company
from models.specification import Specification
from services.planning import (
    spec_check,
)
from test_specifications import GOOD

GOOD_ARCH = {
    "title": "Board service",
    "overview": "An API serves the board; a web client renders it.",
    "modules": [
        {
            "name": "Board API",
            "responsibility": "Lists and moves tasks over HTTP.",
            "path": "src/api/",
            "depends_on": ["Store"],
            "covers": ["REQ-1", "REQ-2"],
        },
        {
            "name": "Store",
            "responsibility": "Keeps the tasks and their status.",
            "path": "src/store/",
            "depends_on": [],
            "covers": [],
        },
        {
            "name": "Web client",
            "responsibility": "Renders the board and drags tasks.",
            "path": "web/",
            "depends_on": ["Board API"],
            "covers": ["REQ-1"],
        },
    ],
    "interfaces": [
        {
            "name": "Tasks HTTP API",
            "kind": "http",
            "module": "Board API",
            "contract": "GET /tasks -> [{id, title, status}]\nPATCH /tasks/{id} {status}",
            "consumers": ["Web client"],
        },
    ],
    "decisions": [
        {
            "title": "SQLite for the store",
            "context": "A single small team.",
            "decision": "Use SQLite through the standard library.",
            "consequences": "No server to run; one writer at a time.",
        },
    ],
    "conventions": ["Tests live beside the module they cover."],
}


def _company(session):
    company = Company(company_name=f"Arch {secrets.token_hex(3)}", autonomy_level=2)
    session.add(company)
    session.commit()
    return company


def _approved_spec(session, company):
    spec = Specification(
        company_id=company.id, status="approved", **spec_check.normalize(GOOD)
    )
    session.add(spec)
    session.commit()
    return spec


class _Daemon:
    jetstream_enabled = False

    def __init__(self, reply=None, replies=None):
        self.reply = reply
        self.replies = dict(replies or {})
        self.requests = []

    async def close(self):
        pass

    async def request(self, subject, payload, timeout=5.0):
        self.requests.append((subject, payload))
        reply = self.replies.get(subject, self.reply)
        if isinstance(reply, Exception):
            raise reply
        return reply
