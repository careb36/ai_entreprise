"""Acceptance tests of the settings file checker, written by a person.

They grade what the factory produced; the model under test never sees or
edits them. Each test is named after the criterion it decides
(``test_req_1_1_...`` decides REQ-1.1); a criterion passes when every test
named after it passes. The project under test is on ``PYTHONPATH``.
"""

import pytest

SCHEMA = {"server": {"port": "int", "host": "str", "debug": "bool"}}


def test_req_1_1_parses_sections_and_keys():
    from confcheck import parse

    text = "[server]\nport = 8080\nhost = example.org\n\n[log]\nlevel=debug\n"
    assert parse(text) == {
        "server": {"port": "8080", "host": "example.org"},
        "log": {"level": "debug"},
    }


def test_req_1_1_values_stay_strings_and_may_hold_equals():
    from confcheck import parse

    assert parse("[db]\nurl = a=b\nempty =\n") == {"db": {"url": "a=b", "empty": ""}}


def test_req_1_2_comments_blank_lines_and_spaces():
    from confcheck import parse

    text = "# a comment\n\n  [ server ]  \n; another\n   port   =   8080   \n\n"
    assert parse(text) == {"server": {"port": "8080"}}


def test_req_1_2_an_empty_text_is_an_empty_config():
    from confcheck import parse

    assert parse("") == {}


@pytest.mark.parametrize(
    ("text", "line"),
    [
        ("[server]\nhost = a\nport 8080\n", 3),
        ("# top\nport = 8080\n", 2),
        ("[server\nport = 1\n", 1),
    ],
)
def test_req_1_3_an_unreadable_line_is_named(text, line):
    from confcheck import ConfigError, parse

    with pytest.raises(ConfigError) as caught:
        parse(text)
    assert caught.value.line == line
    assert str(line) in str(caught.value)


def test_req_1_3_config_error_is_a_value_error():
    from confcheck import ConfigError

    assert issubclass(ConfigError, ValueError)


@pytest.mark.parametrize(
    ("text", "line"),
    [
        ("[server]\nport = 1\nhost = a\nport = 2\n", 4),
        ("[a]\nx = 1\n[b]\ny = 2\n[a]\nz = 3\n", 5),
    ],
)
def test_req_1_4_a_repetition_is_refused(text, line):
    from confcheck import ConfigError, parse

    with pytest.raises(ConfigError) as caught:
        parse(text)
    assert caught.value.line == line


def test_req_2_1_converts_the_values():
    from confcheck.schema import check

    config = {"server": {"port": "8080", "host": "example.org", "debug": "Yes"}}
    assert check(config, SCHEMA) == (
        {"server": {"port": 8080, "host": "example.org", "debug": True}},
        [],
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("true", True),
        ("FALSE", False),
        ("yes", True),
        ("No", False),
        ("on", True),
        ("OFF", False),
        ("1", True),
        ("0", False),
    ],
)
def test_req_2_1_reads_every_bool_spelling(raw, expected):
    from confcheck.schema import check

    values, problems = check({"s": {"flag": raw}}, {"s": {"flag": "bool"}})
    assert (values, problems) == ({"s": {"flag": expected}}, [])


def test_req_2_2_a_wrong_type_is_reported():
    from confcheck.schema import check

    config = {"server": {"port": "eighty", "host": "example.org", "debug": "maybe"}}
    values, problems = check(config, SCHEMA)
    assert sorted(problems) == [
        "server.debug: expected bool",
        "server.port: expected int",
    ]
    assert values == {"server": {"host": "example.org"}}


def test_req_2_3_every_problem_in_one_call():
    from confcheck.schema import check

    config = {
        "server": {"port": "8080", "debug": "no", "colour": "blue"},
        "extra": {"a": "1"},
    }
    values, problems = check(config, SCHEMA)
    assert sorted(problems) == [
        "extra: unknown section",
        "server.colour: unknown",
        "server.host: missing",
    ]
    assert values == {"server": {"port": 8080, "debug": False}}


def test_req_2_3_a_missing_section_reports_its_keys():
    from confcheck.schema import check

    _values, problems = check({}, {"log": {"level": "str"}})
    assert problems == ["log.level: missing"]
