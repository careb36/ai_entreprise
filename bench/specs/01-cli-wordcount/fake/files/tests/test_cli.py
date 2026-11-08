from wordcount.__main__ import main


def test_main_prints_counts(tmp_path, capsys):
    target = tmp_path / "a.txt"
    target.write_text("a b\n", encoding="utf-8")
    assert main([str(target)]) == 0
    assert capsys.readouterr().out.strip() == f"1 2 4 {target}"


def test_main_missing_file(tmp_path, capsys):
    assert main([str(tmp_path / "missing")]) == 1
    assert "missing" in capsys.readouterr().err
