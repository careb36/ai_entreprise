from confcheck.schema import check


def test_converts():
    assert check({"a": {"n": "3", "f": "off"}}, {"a": {"n": "int", "f": "bool"}}) == (
        {"a": {"n": 3, "f": False}},
        [],
    )


def test_reports_everything():
    values, problems = check(
        {"a": {"n": "x", "extra": "1"}, "b": {}}, {"a": {"n": "int", "m": "str"}}
    )
    assert sorted(problems) == [
        "a.extra: unknown",
        "a.m: missing",
        "a.n: expected int",
        "b: unknown section",
    ]
    assert values == {"a": {}}
