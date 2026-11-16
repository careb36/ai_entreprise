"""The ``grounded`` check's delivery-time registry lookups.

PyPI records a package's existence and the exact version, a 404 marks it
missing; lookups are deduplicated, bounded, tried again when a registry could
not be asked and off by the setting; the Go proxy never claims an exact
version.

The Verifier's ``grounded`` check (issue #444): the packages, versions and
imports a delivery names must exist. A registry's answer is an observation
recorded at delivery time (``services.tools.registry_lookup``); only a package
no registry has, an exact version that does not exist or a project import that
names no observed file refutes. The symbols are ``test_grounding_signatures.py``.
"""

from __future__ import annotations

import json

import pytest
from config import settings
from grounding_helpers import _files
from services.tools import registry_lookup


@pytest.fixture
def lookups_on(monkeypatch):
    """``conftest.no_research_tools`` turns the lookups off; these fake the HTTP."""
    monkeypatch.setattr(settings, "GROUNDING_LOOKUPS_ENABLED", True)


class _Http:
    def __init__(self, answers):
        self.answers = answers
        self.requests = []

    def __call__(self, method, url, body=None, timeout=20.0, allow_private=False):
        self.requests.append((method, url, body))
        status, headers, data = self.answers.get(url, (404, {}, b""))
        if isinstance(data, dict):
            data = json.dumps(data).encode()
            headers = {**headers, "content-type": "application/json"}
        return status, headers, data


def _pypi(name, versions, latest):
    return {
        "info": {"name": name, "version": latest, "summary": ""},
        "releases": {version: [{"yanked": False}] for version in versions},
    }


def test_pypi_lookup_records_existence_and_the_exact_version(lookups_on):
    http = _Http(
        {
            f"{settings.RESEARCH_PYPI_URL}/requests/json": (
                200,
                {},
                _pypi("requests", ["2.31.0"], "2.31.0"),
            )
        }
    )
    rows = registry_lookup.observe(
        {},
        _files(("requirements.txt", "requests==2.31.0\n")),
        http=http,
        hosts={"pypi.org"},
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["server"] == "registry" and row["by"] == "verifier"
    assert row["exists"] is True and row["requested_exists"] is True
    assert row["versions_complete"] is True
    assert row["status"] == 200 and row["sha256"]


def test_pypi_404_marks_the_package_as_missing(lookups_on):
    http = _Http({})
    rows = registry_lookup.observe(
        {},
        _files(("requirements.txt", "left-pad==1.0.0\n")),
        http=http,
        hosts={"pypi.org"},
    )
    assert rows[0]["exists"] is False
    assert "no package named" in rows[0]["reason"]
    assert rows[0]["status"] == 404


def test_pypi_missing_exact_version_is_recorded(lookups_on):
    http = _Http(
        {
            f"{settings.RESEARCH_PYPI_URL}/requests/json": (
                200,
                {},
                _pypi("requests", ["2.31.0"], "2.31.0"),
            )
        }
    )
    rows = registry_lookup.observe(
        {},
        _files(("requirements.txt", "requests==9.9.9\n")),
        http=http,
        hosts={"pypi.org"},
    )
    assert rows[0]["exists"] is True and rows[0]["requested_exists"] is False


def test_lookups_are_deduplicated_bounded_and_skipped_when_answered(lookups_on):
    http = _Http(
        {
            f"{settings.RESEARCH_PYPI_URL}/requests/json": (
                200,
                {},
                _pypi("requests", ["2.31.0"], "2.31.0"),
            )
        }
    )
    evidence = {
        "registry_lookups": [
            {
                "ecosystem": "pypi",
                "package": "requests",
                "version": "2.31.0",
                "exists": True,
            }
        ]
    }
    rows = registry_lookup.observe(
        evidence,
        _files(("requirements.txt", "requests==2.31.0\nuvicorn==1.0.0\n")),
        http=http,
        hosts={"pypi.org"},
    )
    assert [row["package"] for row in rows] == ["uvicorn"]
    assert [url for _method, url, _body in http.requests] == [
        f"{settings.RESEARCH_PYPI_URL}/uvicorn/json"
    ]


def test_lookups_are_bounded(lookups_on, monkeypatch):
    monkeypatch.setattr(settings, "GROUNDING_MAX_LOOKUPS", 1)
    http = _Http({})
    rows = registry_lookup.observe(
        {},
        _files(("requirements.txt", "requests==2.31.0\nuvicorn==1.0.0\n")),
        http=http,
        hosts={"pypi.org"},
    )
    assert len(rows) == 1
    assert len(http.requests) == 1


def test_a_registry_that_could_not_be_asked_is_tried_again(lookups_on):
    http = _Http(
        {
            f"{settings.RESEARCH_PYPI_URL}/requests/json": (
                200,
                {},
                _pypi("requests", ["2.31.0"], "2.31.0"),
            )
        }
    )
    evidence = {
        "registry_lookups": [
            {"ecosystem": "pypi", "package": "requests", "exists": None}
        ]
    }
    rows = registry_lookup.observe(
        evidence,
        _files(("requirements.txt", "requests==2.31.0\n")),
        http=http,
        hosts={"pypi.org"},
    )
    assert len(rows) == 1 and rows[0]["exists"] is True


def test_lookups_are_off_when_the_setting_is(monkeypatch):
    monkeypatch.setattr(settings, "GROUNDING_LOOKUPS_ENABLED", False)
    evidence = {}
    assert (
        registry_lookup.observe(
            evidence, _files(("requirements.txt", "requests==2.31.0\n"))
        )
        == []
    )
    assert "registry_lookups" not in evidence


def test_go_proxy_does_not_claim_the_exact_version(lookups_on):
    http = _Http(
        {
            f"{settings.RESEARCH_GO_URL}/github.com/x/y/@latest": (
                200,
                {},
                {"Version": "v1.2.3", "Time": "2026-01-01T00:00:00Z"},
            )
        }
    )
    rows = registry_lookup.observe(
        {},
        _files(("go.mod", "module example.com/app\nrequire github.com/x/y v1.2.2\n")),
        http=http,
        hosts={"proxy.golang.org"},
    )
    row = rows[0]
    assert row["exists"] is True
    assert row["requested_exists"] is None
    assert row["versions_complete"] is False
