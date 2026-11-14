"""Which deliverables the quality gate holds to the documentation rules (#508)."""

from services.loop.worker import quality_gate
from services.loop.worker.quality_gate import _reads_as_documentation

PROSE = " ".join(["word"] * 200)


def _code(path="notesapi/store.py"):
    return (
        f"Delivered the store.\n\n```python path={path}\nSTORE = {{}}\n```\n\n{PROSE}\n"
    )


def test_a_code_task_named_with_api_inside_a_word_is_not_documentation():
    for title in (
        "Build notesapi store",
        "Rapid prototype of the parser",
        "Capital gains",
    ):
        assert _reads_as_documentation({"title": title}, PROSE) is False


def test_keywords_still_count_as_whole_words():
    assert _reads_as_documentation({"title": "Write the README"}, PROSE) is True
    assert _reads_as_documentation({"title": "Document the API"}, PROSE) is True
    assert _reads_as_documentation({"title": "Add a processor"}, PROSE) is False


def test_an_api_task_delivering_code_is_code():
    api_task = {"title": "Build the REST API", "description": "JSON over HTTP."}
    assert _reads_as_documentation(api_task, _code()) is False


def test_a_delivery_of_documents_is_documentation_whatever_the_wording():
    doc = f"```markdown path=docs/API.md\n# API\n```\n\n{PROSE}"
    assert _reads_as_documentation({"title": "Store"}, doc) is True


def test_a_document_task_delivering_code_is_still_held_to_the_document_rules():
    task = {"title": "Create testing strategy document"}
    assert _reads_as_documentation(task, _code("src/strategy.py")) is True


def test_a_headless_code_delivery_for_an_api_task_passes_the_gate():
    ok, reason = quality_gate._validate_minimum_deliverable(
        {"title": "Build the notesapi store", "description": "A JSON API in memory."},
        _code(),
    )
    assert (ok, reason) == (True, "ok")


def test_a_documentation_task_without_headings_is_still_sent_back():
    ok, reason = quality_gate._validate_minimum_deliverable(
        {"title": "Write the architecture document"}, PROSE + "."
    )
    assert not ok and "structured sections" in reason
