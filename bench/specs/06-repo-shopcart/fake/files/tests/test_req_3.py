import json


def _file(tmp_path):
    path = tmp_path / "cart.json"
    path.write_text(json.dumps([{"sku": "tea", "price": "50.00"}]))
    return str(path)


def test_req_3_1_code_option(tmp_path, capsys):
    from shopcart.__main__ import main

    assert main([_file(tmp_path), "--code", "SAVE10"]) == 0
    assert capsys.readouterr().out == "Total: 45.00\n"


def test_req_3_2_unknown_code(tmp_path, capsys):
    from shopcart.__main__ import main

    assert main([_file(tmp_path), "--code", "NOPE"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "unknown discount code: NOPE" in captured.err
