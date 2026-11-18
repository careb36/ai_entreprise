import json

from shopcart.__main__ import main


def test_prints_the_total(tmp_path, capsys):
    path = tmp_path / "cart.json"
    path.write_text(json.dumps([{"sku": "tea", "price": "2.50", "quantity": 2}]))
    assert main([str(path)]) == 0
    assert capsys.readouterr().out == "Total: 5.00\n"


def test_a_missing_file_exits_1(tmp_path, capsys):
    assert main([str(tmp_path / "none.json")]) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err.startswith("error:")


def test_a_bad_line_exits_1(tmp_path, capsys):
    path = tmp_path / "cart.json"
    path.write_text(json.dumps([{"sku": "tea"}]))
    assert main([str(path)]) == 1
