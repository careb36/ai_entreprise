import json

from shopcart.__main__ import main


def _file(tmp_path):
    path = tmp_path / "cart.json"
    path.write_text(json.dumps([{"sku": "tea", "price": "20.00"}]))
    return path


def test_the_code_option(tmp_path, capsys):
    assert main([str(_file(tmp_path)), "--code", "SAVE10"]) == 0
    assert capsys.readouterr().out == "Total: 18.00\n"


def test_an_unknown_code_exits_2(tmp_path, capsys):
    assert main([str(_file(tmp_path)), "--code", "X"]) == 2
    captured = capsys.readouterr()
    assert captured.out == "" and "unknown discount code: X" in captured.err
