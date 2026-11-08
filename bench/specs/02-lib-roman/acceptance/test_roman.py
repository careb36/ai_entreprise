"""Acceptance tests of the Roman numerals library, written by a person.

They grade what the factory produced; the model under test never sees or
edits them. Each test is named after the criterion it decides
(``test_req_1_1_...`` decides REQ-1.1); a criterion passes when every test
named after it passes. The project under test is on ``PYTHONPATH``.
"""

import pytest

KNOWN = [(1, "I"), (4, "IV"), (9, "IX"), (42, "XLII"), (1994, "MCMXCIV"), (3999, "MMMCMXCIX")]


@pytest.mark.parametrize(("number", "numeral"), KNOWN)
def test_req_1_1_writes_known_values(number, numeral):
    from romannum import to_roman

    assert to_roman(number) == numeral


@pytest.mark.parametrize("number", [0, -1, 4000])
def test_req_1_2_refuses_out_of_range(number):
    from romannum import to_roman

    with pytest.raises(ValueError):
        to_roman(number)


@pytest.mark.parametrize(("number", "numeral"), KNOWN)
def test_req_2_1_reads_known_values(number, numeral):
    from romannum import from_roman

    assert from_roman(numeral) == number


@pytest.mark.parametrize("text", ["IIII", "IC", "ABC", "", "iv", "MMMM"])
def test_req_2_2_refuses_non_standard_text(text):
    from romannum import from_roman

    with pytest.raises(ValueError):
        from_roman(text)


def test_req_3_1_round_trip_every_number():
    from romannum import from_roman, to_roman

    assert [from_roman(to_roman(n)) for n in range(1, 4000)] == list(range(1, 4000))
