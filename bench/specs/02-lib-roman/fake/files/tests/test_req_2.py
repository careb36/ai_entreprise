import pytest


def test_req_2_1_reads_known_values():
    from romannum import from_roman

    assert [from_roman(s) for s in ("I", "IV", "XLII", "MCMXCIV", "MMMCMXCIX")] == [1, 4, 42, 1994, 3999]


def test_req_2_2_refuses_non_standard_text():
    from romannum import from_roman

    for text in ("IIII", "IC", "ABC", ""):
        with pytest.raises(ValueError):
            from_roman(text)
