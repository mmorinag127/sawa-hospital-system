"""Copy identity-checked prod menu originals to a staging-only verification prefix."""

import hashlib
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from compare_stg_prod_db import _make_connector, _connect, _load_secret, PROD_CONN, PROD_SECRET
from src.services import menu_service

DESTINATION = "gs://sawahospitalsystem-stg-raw/verification/menu-master-source-20261001/"


def main():
    folder = Path(__file__).resolve().parents[1] / "tmp/menu-verification"
    folder.mkdir(parents=True, exist_ok=True)
    manifest = {"cases": [], "production_masters": {}}
    with _make_connector() as connector:
        connection = _connect(connector, PROD_CONN, _load_secret(PROD_SECRET))
        try:
            cursor = connection.cursor()
            cursor.execute("SET TRANSACTION READ ONLY")
            cursor.execute("SELECT row_to_json(t) FROM menu_masters t")
            for row in cursor.fetchall():
                master = row[0]
                manifest["production_masters"][master["normalized_name"]] = {
                    field: master.get(field) for field in (
                        "name", "unit_type", "qty_per_serving", "temp_type", "category",
                        "bag_max_qty", "bag_max_unit", "condiments",
                    )
                }
            for month in ("2026-09", "2026-10", "2026-11", "2026-12"):
                cursor.execute(
                    "SELECT metadata FROM audit_logs WHERE action=%s AND target=%s ORDER BY created_at DESC LIMIT 1",
                    ("menu_upload", month),
                )
                metadata = cursor.fetchone()[0]
                raw = subprocess.run(["gcloud", "storage", "cat", metadata["file_uri"]],
                                     capture_output=True, check=True).stdout
                assert hashlib.sha256(raw).hexdigest() == metadata["content_sha256"]
                month_start, _, items, entries = menu_service._parse_monthly_menu(raw, metadata["filename"], None, month)
                assert month_start.strftime("%Y-%m") == month
                assert all(item.get("unit_type") is None and item.get("qty_per_serving") is None for item in items)
                path = folder / (month + ".xlsm")
                path.write_bytes(raw)
                uri = DESTINATION + path.name
                subprocess.run(["gcloud", "storage", "cp", str(path), uri], check=True)
                expected = {}
                for name in ("サバの味噌煮　添)ﾎｰﾚﾝ草", "玉子焼き"):
                    assert any(item["name"] == name for item in items)
                    master = manifest["production_masters"][menu_service._normalize_menu_name(name)]
                    expected[name] = [master["unit_type"], master["qty_per_serving"]]
                manifest["cases"].append({
                    "month": month, "uri": uri, "source_uri": metadata["file_uri"],
                    "read_only_baseline": month == "2026-09",
                    "sha256": metadata["content_sha256"], "quantity_fields_absent_verified": True,
                    "expected_entries": len(entries), "expected_quantities": expected,
                })
            connection.rollback()
        finally:
            connection.close()
    path = folder / "manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    subprocess.run(["gcloud", "storage", "cp", str(path), DESTINATION + path.name], check=True)
    print(DESTINATION + path.name)


if __name__ == "__main__":
    main()
