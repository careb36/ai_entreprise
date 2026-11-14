import pytest
from salesjob.summary import summarize


def test_sums_per_region():
    result = summarize("region,product,amount\na,x,1\na,y,0.5\nb,x,2\n")
    assert result["regions"] == {
        "a": {"count": 2, "total": "1.50"},
        "b": {"count": 1, "total": "2.00"},
    }
    assert (result["total"], result["rows"], result["bad"]) == ("3.50", 3, [])


def test_bad_row_has_its_line():
    result = summarize("region,product,amount\na,x,oops\n")
    assert result["bad"][0]["line"] == 2
    assert result["rows"] == 0


def test_header_without_amount():
    with pytest.raises(ValueError):
        summarize("region,product\na,x\n")
