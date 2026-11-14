import pytest


def test_req_1_1_parses():
    from confcheck import parse

    text = "[server]\nport = 8080\nhost = example.org\n\n[log]\nlevel=debug\n"
    assert parse(text) == {
        "server": {"port": "8080", "host": "example.org"},
        "log": {"level": "debug"},
    }


def test_req_1_2_ignores_comments_and_trims():
    from confcheck import parse

    assert parse("; c\n# d\n\n [ s ] \n  k  =  v  \n") == {"s": {"k": "v"}}


def test_req_1_3_names_the_bad_line():
    from confcheck import ConfigError, parse

    with pytest.raises(ConfigError) as caught:
        parse("[server]\nhost = a\nport 8080\n")
    assert caught.value.line == 3 and "3" in str(caught.value)
    with pytest.raises(ConfigError):
        parse("port = 1\n")


def test_req_1_4_refuses_repetitions():
    from confcheck import ConfigError, parse

    with pytest.raises(ConfigError) as caught:
        parse("[s]\nk = 1\nk = 2\n")
    assert caught.value.line == 3
    with pytest.raises(ConfigError) as caught:
        parse("[s]\n[s]\n")
    assert caught.value.line == 2
