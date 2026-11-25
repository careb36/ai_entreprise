"""The self-hosting company gets La Fabrique's own rules as procedures
(``scripts/self_host/company_rules.py``): written once, a 409 (already active
or proposed) left alone, any other refusal raised."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_PATH = (
    Path(__file__).resolve().parents[2] / "scripts" / "self_host" / "company_rules.py"
)
_SPEC = importlib.util.spec_from_file_location("self_host_company_rules", _PATH)
company_rules = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(company_rules)


class Refused(RuntimeError):
    def __init__(self, status):
        super().__init__(f"-> {status}")
        self.status = status


class Api:
    def __init__(self, refuse=None):
        self.posts, self.refuse = [], refuse or {}

    def post(self, path, body):
        self.posts.append((path, body))
        status = self.refuse.get(body["role"])
        if status:
            raise Refused(status)
        return {"id": len(self.posts)}


def test_the_rule_on_test_files_reaches_qa_and_the_developer():
    roles = {p["role"] for p in company_rules.PROCEDURES}
    assert roles == {"QA", "Developer"}
    for rule in company_rules.PROCEDURES:
        assert "docs/architecture/contexts.json" in rule["body"]
        assert len(rule["title"]) <= 120 and len(rule["body"]) <= 2000


def test_missing_procedures_are_written_and_existing_ones_left_alone():
    api = Api(refuse={"Developer": 409})
    seen = company_rules.ensure(api, 3)
    assert [path for path, _ in api.posts] == ["/v1/procedures", "/v1/procedures"]
    assert all(body["company_id"] == 3 for _, body in api.posts)
    assert [(s["role"], s["created"]) for s in seen] == [
        ("QA", True),
        ("Developer", False),
    ]


def test_another_refusal_is_raised():
    with pytest.raises(Refused):
        company_rules.ensure(Api(refuse={"QA": 403}), 3)
