"""Read the deployed label download and retain its exact CSV as evidence."""

import argparse
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import urllib.parse
import urllib.request


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", choices=("stg", "prod"), required=True)
    parser.add_argument("--order-id", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    url = (
        f"https://web-{args.target}-avlnzjjrca-dt.a.run.app/api/outputs/labels?"
        + urllib.parse.urlencode({"order_id": args.order_id})
    )
    request = urllib.request.Request(
        url, headers={"Authorization": "Bearer " + os.environ["VERIFICATION_TOKEN"]}
    )
    with urllib.request.urlopen(request, timeout=240) as response:
        data = response.read()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "labels.csv").write_bytes(data)
    rows = list(csv.DictReader(io.StringIO(data.decode("cp932"))))
    breakfast = [row for row in rows if row.get("時間", "").startswith("朝")]
    assert breakfast, "Live order has no breakfast labels; choose an order with breakfast"
    assert all(not row["メニュー"].startswith("主菜") for row in breakfast), "Breakfast main-dish label remains"
    assert any(row["メニュー"].startswith("副菜①") for row in breakfast), "Breakfast side-dish-one label missing"
    summary = {
        "url": url, "verification_commit": os.environ.get("GITHUB_SHA"),
        "csv_sha256": hashlib.sha256(data).hexdigest(), "rows": len(rows),
        "breakfast_rows": len(breakfast),
        "categories_by_daypart": {
            part: sorted({row["メニュー"] for row in rows if row.get("時間", "").startswith(part)})
            for part in ("朝", "昼", "夕")
        },
    }
    report = json.dumps(summary, ensure_ascii=False, indent=2)
    (args.output_dir / "summary.json").write_text(report, encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
