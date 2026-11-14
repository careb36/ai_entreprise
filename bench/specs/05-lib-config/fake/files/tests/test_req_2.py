SCHEMA = {"server": {"port": "int", "host": "str", "debug": "bool"}}


def test_req_2_1_converts():
    from confcheck.schema import check

    config = {"server": {"port": "8080", "host": "example.org", "debug": "Yes"}}
    assert check(config, SCHEMA) == (
        {"server": {"port": 8080, "host": "example.org", "debug": True}},
        [],
    )


def test_req_2_2_wrong_type():
    from confcheck.schema import check

    _, problems = check(
        {"server": {"port": "eighty", "host": "h", "debug": "maybe"}}, SCHEMA
    )
    assert sorted(problems) == [
        "server.debug: expected bool",
        "server.port: expected int",
    ]


def test_req_2_3_missing_unknown_and_extra_section():
    from confcheck.schema import check

    _, problems = check(
        {"server": {"port": "1", "debug": "0", "x": "1"}, "other": {}}, SCHEMA
    )
    assert sorted(problems) == [
        "other: unknown section",
        "server.host: missing",
        "server.x: unknown",
    ]
