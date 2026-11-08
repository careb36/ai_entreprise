def test_req_1_1_counts_a_two_line_text():
    from wordcount import count

    assert count("one two\nthree\n") == {"lines": 2, "words": 3, "chars": 14}


def test_req_1_2_counts_an_empty_text():
    from wordcount import count

    assert count("") == {"lines": 0, "words": 0, "chars": 0}
