"""Read-only replay of archived monthly-menu inputs; never saves menu data."""

import hashlib
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from compare_stg_prod_db import _make_connector, _connect, _load_secret, PROD_CONN, PROD_SECRET
from src.models.menu import MenuMaster
from src.services import menu_service as service


def main():
    with _make_connector() as connector:
        connection = _connect(connector, PROD_CONN, _load_secret(PROD_SECRET))
        try:
            cursor = connection.cursor()
            cursor.execute("SET TRANSACTION READ ONLY")
            cursor.execute("SELECT row_to_json(t) FROM menu_masters t")
            masters = {row[0]["normalized_name"]: MenuMaster(**{
                key: value for key, value in row[0].items() if key != "updated_at"
            }) for row in cursor.fetchall()}
            cursor.execute("SELECT row_to_json(t) FROM menu_rules t WHERE active = true")
            rules = [row[0] for row in cursor.fetchall()]
            for month in ("2026-09", "2026-10", "2026-11", "2026-12"):
                cursor.execute(
                    "SELECT metadata FROM audit_logs WHERE action = %s AND target = %s ORDER BY created_at DESC LIMIT 1",
                    ("menu_upload", month),
                )
                metadata = cursor.fetchone()[0]
                raw = subprocess.run(
                    ["gcloud", "storage", "cat", metadata["file_uri"]],
                    capture_output=True, check=True,
                ).stdout
                assert hashlib.sha256(raw).hexdigest() == metadata["content_sha256"]
                _, _, items, _ = service._parse_monthly_menu(raw, metadata["filename"], None, month)
                enriched = service._apply_rule_payloads_to_items(items, rules)
                changed = []
                for source, item in zip(items, enriched, strict=True):
                    master = masters.get(service._normalize_menu_name(source["name"]))
                    if master is None:
                        continue
                    patch = service._monthly_item_patch_from_source(item, master)
                    for field in ("unit_type", "qty_per_serving"):
                        if source.get(field) is not None:
                            assert patch[field] == source[field]
                        elif getattr(master, field) is not None:
                            assert patch[field] == getattr(master, field)
                    before = (item.get("unit_type"), item.get("qty_per_serving"))
                    after = (patch.get("unit_type"), patch.get("qty_per_serving"))
                    if before != after:
                        changed.append({"name": source["name"], "before": before, "after": after})
                print(json.dumps({"month": month, "sha256": metadata["content_sha256"],
                                  "changed_count": len(changed), "changes": changed}, ensure_ascii=False), flush=True)
            connection.rollback()
        finally:
            connection.close()


if __name__ == "__main__":
    main()
