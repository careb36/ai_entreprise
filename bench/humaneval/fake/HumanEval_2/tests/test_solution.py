from solution import truncate_number


def test_an_integer_has_no_decimal_part():
    assert truncate_number(4.0) == 0.0


def test_the_decimal_part_is_kept():
    assert abs(truncate_number(7.25) - 0.25) < 1e-9
