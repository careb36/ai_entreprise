from solution import has_close_elements


def test_neighbours_after_sorting_decide():
    assert has_close_elements([5.0, 1.0, 4.9], 0.2)
    assert not has_close_elements([5.0, 1.0, 4.0], 0.5)


def test_fewer_than_two_numbers_are_never_close():
    assert not has_close_elements([], 1.0)
    assert not has_close_elements([1.0], 1.0)
