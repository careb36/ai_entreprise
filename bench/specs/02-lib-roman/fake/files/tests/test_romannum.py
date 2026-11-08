import pytest
from romannum import from_roman, to_roman


def test_round_trip_small():
    assert [from_roman(to_roman(n)) for n in range(1, 50)] == list(range(1, 50))


def test_refuses_zero():
    with pytest.raises(ValueError):
        to_roman(0)
