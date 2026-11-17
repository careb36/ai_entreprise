"""Read pytest's JUnit report without trusting it.

The report is written by pytest running the person-written acceptance tests,
but inside a project the factory's agents produced: a ``conftest.py`` or a
plugin there can rewrite it. ``xml.etree`` would expand the entities a
document type declares (an "XML bomb") and the standard library cannot be told
to refuse them, so this reads the report with ``expat`` directly and refuses
any document type, entity declaration or external entity before anything is
expanded. ``defusedxml`` does the same, but the bench's scripts run on the
runner's ``python3`` with the standard library only.

Semgrep's ``use-defused-xml`` (p/security-audit) flagged the ``xml.etree``
import this replaces (issue #522). The rule matches any import from ``xml``,
this one included, so the import below carries a ``nosemgrep`` naming that
rule: what it guards against, entity expansion, is refused by the handlers in
``testcases`` and held by ``backend/tests/test_bench_grading.py``.
"""

from __future__ import annotations

# Every DTD and entity is refused below, before expansion (see the docstring).
# nosemgrep: python.lang.security.use-defused-xml.use-defused-xml
from xml.parsers import expat

# A test case with one of these as a direct child did not pass.
NOT_PASSED = ("failure", "error", "skipped")
PASS, FAIL, SKIP = "pass", "fail", "skip"


class JUnitError(ValueError):
    """The report is not well-formed XML or declares a DTD or an entity."""


def _refuse(*_args: object) -> None:
    raise JUnitError("a document type or entity declaration is not accepted")


def testcases(xml_text: str) -> list[tuple[str, bool]]:
    """Each ``<testcase>`` as ``(name, passed)``, in document order.

    ``passed`` is False when the case has a ``failure``, ``error`` or
    ``skipped`` child. Raises ``JUnitError`` on malformed XML and on any
    ``<!DOCTYPE``, entity declaration or external entity reference.
    """
    return [(name, outcome == PASS) for name, outcome in outcomes(xml_text)]


def outcomes(xml_text: str) -> list[tuple[str, str]]:
    """Each ``<testcase>`` as ``(name, outcome)``, in document order: ``fail``
    with a ``failure`` or ``error`` child, else ``skip`` with a ``skipped``
    one, else ``pass``. A skip is told apart from a failure because pytest
    exits 0 on a run of passes and skips (review of PR #634). Raises
    ``JUnitError`` as ``testcases`` does."""
    parser = expat.ParserCreate()
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
    parser.StartDoctypeDeclHandler = _refuse
    parser.EntityDeclHandler = _refuse
    parser.UnparsedEntityDeclHandler = _refuse
    parser.ExternalEntityRefHandler = _refuse

    stack: list[str] = []
    cases: list[list] = []

    def start(name: str, attrs: dict[str, str]) -> None:
        if name == "testcase":
            cases.append([attrs.get("name", ""), PASS])
        elif name in NOT_PASSED and stack and stack[-1] == "testcase":
            if name != "skipped":
                cases[-1][1] = FAIL
            elif cases[-1][1] == PASS:
                cases[-1][1] = SKIP
        stack.append(name)

    def end(_name: str) -> None:
        stack.pop()

    parser.StartElementHandler = start
    parser.EndElementHandler = end
    try:
        parser.Parse(xml_text, True)
    except expat.ExpatError as exc:
        raise JUnitError(str(exc)) from exc
    return [(name, outcome) for name, outcome in cases]
