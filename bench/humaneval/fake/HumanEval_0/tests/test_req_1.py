def test_req_1_1_docstring_examples():
    from solution import has_close_elements

    assert has_close_elements([1.0, 2.0, 3.0], 0.5) is False
    assert has_close_elements([1.0, 2.8, 3.0, 4.0, 5.0, 2.0], 0.3) is True
