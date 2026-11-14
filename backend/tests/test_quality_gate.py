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


def test_keywords_still_count_as_whole_words_without_file_blocks():
    assert _reads_as_documentation({"title": "Write the README"}, PROSE) is True
    assert _reads_as_documentation({"title": "Document the API"}, PROSE) is True
    assert _reads_as_documentation({"title": "Add a processor"}, PROSE) is False


def test_what_is_delivered_decides_before_the_wording():
    api_task = {"title": "Build the REST API", "description": "The data pipeline."}
    assert _reads_as_documentation(api_task, _code()) is False
    doc = f"```markdown path=docs/API.md\n# API\n```\n\n{PROSE}"
    assert _reads_as_documentation({"title": "Store"}, doc) is True
    mixed = _code() + "\n```markdown path=README.md\n# Notes\n```\n"
    assert _reads_as_documentation({"title": "Write the README"}, mixed) is False


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
