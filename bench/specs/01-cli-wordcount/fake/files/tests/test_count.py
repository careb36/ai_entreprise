from wordcount import count


def test_count_two_lines():
    assert count("one two\nthree\n") == {"lines": 2, "words": 3, "chars": 14}


def test_count_empty():
    assert count("") == {"lines": 0, "words": 0, "chars": 0}
