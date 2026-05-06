from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_services_do_not_run_schema_repairs_at_import_or_read_time() -> None:
    forbidden = (
        "Base.metadata.create_all",
        "ALTER TABLE",
        "__table__.create",
        "CREATE INDEX",
        "DROP CONSTRAINT",
    )
    offenders: list[str] = []
    for path in sorted((ROOT / "src" / "services").glob("*.py")):
        text = path.read_text(encoding="utf-8")
        for token in forbidden:
            if token in text:
                offenders.append(f"{path.relative_to(ROOT)}: {token}")
    assert offenders == []
