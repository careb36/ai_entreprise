import pytest


def test_req_1_1_sums_per_region():
    from salesjob.summary import summarize

    result = summarize(
        "region,product,amount\nnorth,tea,2.50\nnorth,coffee,3\nsouth,tea,1.25\n"
    )
    assert result["regions"] == {
        "north": {"count": 2, "total": "5.50"},
        "south": {"count": 1, "total": "1.25"},
    }
    assert (result["total"], result["rows"], result["bad"]) == ("6.75", 3, [])


def test_req_1_2_bad_rows_are_left_out():
    from salesjob.summary import summarize

    result = summarize(
        "region,product,amount\nnorth,tea,x\nnorth,tea,-2\n,tea,1\nnorth,tea\nnorth,tea,1\n"
    )
    assert [b["line"] for b in result["bad"]] == [2, 3, 4, 5]
    assert result["total"] == "1.00"


def test_req_1_3_needs_an_amount_column():
    from salesjob.summary import summarize

    for text in ("region,product\nnorth,tea\n", ""):
        with pytest.raises(ValueError):
            summarize(text)
