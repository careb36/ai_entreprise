import pytest
from confcheck import ConfigError, parse


def test_sections_and_keys():
    assert parse("[a]\nx = 1\n# c\n[b]\ny=2\n") == {"a": {"x": "1"}, "b": {"y": "2"}}


def test_bad_line_names_its_number():
    with pytest.raises(ConfigError) as caught:
        parse("[a]\nnope\n")
    assert caught.value.line == 2


def test_repeated_key():
    with pytest.raises(ConfigError) as caught:
        parse("[a]\nx = 1\nx = 2\n")
    assert caught.value.line == 3
