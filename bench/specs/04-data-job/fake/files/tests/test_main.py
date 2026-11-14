import json

from salesjob.__main__ import main


def test_json_output(tmp_path, capsys):
    path = tmp_path / "s.csv"
    path.write_text("region,product,amount\na,x,1\n", encoding="utf-8")
    assert main([str(path)]) == 0
    assert json.loads(capsys.readouterr().out)["bad_rows"] == 0


def test_bad_rows_exit_2(tmp_path, capsys):
    path = tmp_path / "s.csv"
    path.write_text("region,product,amount\na,x,1\n,x,2\n", encoding="utf-8")
    assert main([str(path)]) == 2
    assert "line 3:" in capsys.readouterr().err


def test_missing_file_exit_1(tmp_path, capsys):
    assert main([str(tmp_path / "none.csv")]) == 1
    assert capsys.readouterr().out == ""


def test_csv_output(tmp_path, capsys):
    path = tmp_path / "s.csv"
    path.write_text("region,product,amount\nb,x,1\na,x,2\n", encoding="utf-8")
    assert main([str(path), "--format", "csv"]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "region,count,total",
        "a,1,2.00",
        "b,1,1.00",
    ]
