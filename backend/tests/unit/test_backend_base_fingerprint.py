from __future__ import annotations

from pathlib import Path

from scripts.backend_base_fingerprint import BASE_INPUTS, main


def test_backend_base_fingerprint_changes_when_base_input_changes(tmp_path: Path, capsys) -> None:
    for relative in BASE_INPUTS:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{relative}\n", encoding="utf-8")

    assert main_for_path(tmp_path) == 0
    first = capsys.readouterr().out.strip()

    (tmp_path / "requirements.txt").write_text("changed\n", encoding="utf-8")

    assert main_for_path(tmp_path) == 0
    second = capsys.readouterr().out.strip()
    assert first != second


def main_for_path(path: Path) -> int:
    import sys

    original = sys.argv
    try:
        sys.argv = ["backend_base_fingerprint.py", str(path)]
        return main()
    finally:
        sys.argv = original
