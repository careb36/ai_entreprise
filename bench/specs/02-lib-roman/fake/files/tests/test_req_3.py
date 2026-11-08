def test_req_3_1_round_trip():
    from romannum import from_roman, to_roman

    assert all(from_roman(to_roman(n)) == n for n in range(1, 4000))
