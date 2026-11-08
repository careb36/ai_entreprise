import pytest


def test_req_1_1_writes_known_values():
    from romannum import to_roman

    assert [to_roman(n) for n in (1, 4, 42, 1994, 3999)] == ["I", "IV", "XLII", "MCMXCIV", "MMMCMXCIX"]


def test_req_1_2_refuses_out_of_range():
    from romannum import to_roman

    for n in (0, -1, 4000):
        with pytest.raises(ValueError):
            to_roman(n)
