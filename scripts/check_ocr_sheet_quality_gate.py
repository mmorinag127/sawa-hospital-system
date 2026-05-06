#!/usr/bin/env python3
import json
import re
import statistics
import sys


def parse_num(value):
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if not re.fullmatch(r"-?\d+(?:\.\d+)?", text):
        return None
    return float(text)


def main() -> int:
    path = sys.argv[1]
    strict = sys.argv[2] == "1"
    min_ratio = float(sys.argv[3])
    abs_max_qty = float(sys.argv[4])
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    fields = data.get("fields") or []
    rows = data.get("rows") or []
    apply_blockers = data.get("apply_blockers") or []
    warnings = data.get("warnings") or []
    if not fields:
        raise SystemExit("invalid response: fields is empty")
    if "menu" not in fields:
        raise SystemExit("invalid response: menu field missing")
    qty_indexes = [idx for idx, field in enumerate(fields) if str(field).startswith("qty.")]
    if not qty_indexes:
        raise SystemExit("invalid response: qty.* fields missing")
    blocked_empty = not rows
    if blocked_empty:
        allowed_blockers = {"rows_empty", "draft_rows_empty", "menu_entries_missing", "monthly_menu_object_missing"}
        blocker_codes = {str(code) for code in apply_blockers}
        warning_codes = {str(code) for code in warnings}
        if "rows_empty" not in blocker_codes:
            raise SystemExit("invalid response: rows is empty without rows_empty blocker")
        if not (blocker_codes | warning_codes) & allowed_blockers:
            raise SystemExit(
                "invalid response: rows is empty without an explicit blocked-sheet reason "
                f"blockers={apply_blockers} warnings={warnings}"
            )

    row_numeric_counts = []
    values = []
    for row in rows:
        if not isinstance(row, list):
            row = []
        count = 0
        for col in qty_indexes:
            if col >= len(row):
                continue
            val = parse_num(row[col])
            if val is None:
                continue
            values.append(val)
            count += 1
        row_numeric_counts.append(count)

    filled_rows = sum(1 for count in row_numeric_counts if count > 0)
    filled_ratio = (filled_rows / len(rows)) if rows else 0.0
    warnings = data.get("warnings") or []
    source = str(data.get("source") or "")

    if strict:
        if warnings and not blocked_empty:
            raise SystemExit(f"ocr-sheet gate failed: warnings present: {warnings}")
        if not values and not blocked_empty:
            raise SystemExit("ocr-sheet gate failed: no numeric quantity cell")
        if source.startswith("weekly_menu") and not blocked_empty and filled_ratio < min_ratio:
            raise SystemExit(
                f"ocr-sheet gate failed: filled_row_ratio={filled_ratio:.3f} < {min_ratio:.3f}"
            )
        max_qty = max(values) if values else 0
        if max_qty > abs_max_qty:
            raise SystemExit(f"ocr-sheet gate failed: max_qty={max_qty:g} > {abs_max_qty:g}")
        positives = [v for v in values if v > 0]
        if positives:
            median = statistics.median(positives)
            spike_threshold = max(median * 3.5, 15.0)
            if max_qty > spike_threshold:
                raise SystemExit(
                    f"ocr-sheet gate failed: spike max_qty={max_qty:g} median={median:g} threshold={spike_threshold:g}"
                )

    print(
        f"ok: fields={len(fields)} rows={len(rows)} source={source} "
        f"qty_cells={len(values)} filled_row_ratio={filled_ratio:.3f} "
        f"max_qty={(max(values) if values else 0):g}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
