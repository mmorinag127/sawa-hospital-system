# テンプレート自動生成（施設別）: PDF入力 → テンプレPNG/JSON生成

このドキュメントは、**各施設の注文書PDF（1ページ）を入力として**、
- テンプレ参照画像 `template.png`
- Firestore登録用のテンプレ定義 `template.json`（ROI/後処理ルールの初期案）
- デバッグ用の中間画像（線検出など）

を **自動生成**するためのコード一式です。

目的は「施設ごとにフォーマット（区分）が異なる」状況で、**施設別テンプレ（ROI座標）を最短で立ち上げる**ことです。
※自動推定が合わない場合に備えて、`--rows/--cols` などの手動補助オプションも用意しています。

---

## 1. 何を自動化するか（方針）

1) PDF → 高DPI画像化（350〜400dpi）
- poppler `pdftoppm` を使用

2) 罫線の水平/垂直成分を形態学で抽出し、表領域（バウンディングボックス）を推定
- OpenCVの形態学（erode/dilate + custom kernel）は線抽出の定石です。

3) 表領域の内部で、縦線/横線の投影ヒストグラムから **グリッド線座標**を推定し、セルROIを作成
- 行列数が分かっている場合は `--rows/--cols` で補正

4) 施設名枠・備考枠などの大きな矩形ROIを、ページ上部/下部からヒューリスティックに推定（失敗時は固定比率フォールバック）

---

## 2. 生成物

- `out/<facility_id>/template.png` … 参照画像（この施設のテンプレ）
- `out/<facility_id>/template.json` … Firestore `templates/{template_id}` 用の初期JSON
- `out/<facility_id>/debug_lines.png` … 罫線抽出デバッグ
- `out/<facility_id>/debug_table_bbox.png` … 表領域推定デバッグ

---

## 3. 使い方

### 3.1 ローカル（またはCloud Runビルド環境）で実行
```bash
pip install opencv-python-headless numpy pillow
sudo apt-get install -y poppler-utils

python template_builder.py \
  --pdf /path/to/facilityA.pdf \
  --facility FAC_A \
  --template-id TPL_FAC_A_v1 \
  --rows 7 --cols 6 \
  --out out
```

- `--rows/--cols` は「数量表の行数/列数（セル数）」です。
  - 行数=曜日/日付の行数、列数=常食2F/3F…の列数
- 行列が施設によって違う場合、施設テンプレごとにここを変えます。

---

## 4. コード（template_builder.py）

> そのまま `template_builder.py` として保存してください。

```python
#!/usr/bin/env python3
import argparse
import json
import subprocess
import tempfile
from pathlib import Path
from typing import List, Tuple, Optional

import cv2
import numpy as np


def run_pdftoppm_first_page(pdf_path: Path, dpi: int) -> np.ndarray:
    """Render first page of a PDF to a BGR image using pdftoppm."""
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        out_prefix = d / "page"
        cmd = [
            "pdftoppm",
            "-f", "1",
            "-l", "1",
            "-png",
            "-rx", str(dpi),
            "-ry", str(dpi),
            str(pdf_path),
            str(out_prefix),
        ]
        subprocess.check_call(cmd)
        png_path = d / "page-1.png"
        data = png_path.read_bytes()
    n = np.frombuffer(data, dtype=np.uint8)
    img = cv2.imdecode(n, cv2.IMREAD_COLOR)
    if img is None:
        raise RuntimeError("Failed to decode rendered PNG.")
    return img


def ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def save_png(path: Path, img_bgr: np.ndarray) -> None:
    ensure_dir(path.parent)
    ok = cv2.imwrite(str(path), img_bgr)
    if not ok:
        raise RuntimeError(f"Failed to write image: {path}")


def to_gray(img_bgr: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)


def otsu_bin(gray: np.ndarray) -> np.ndarray:
    # Return binary with background white (255), foreground black (0)
    den = cv2.fastNlMeansDenoising(gray, None, h=10, templateWindowSize=7, searchWindowSize=21)
    _, b = cv2.threshold(den, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return b


def extract_lines(bin_img: np.ndarray, h_ksize: int, v_ksize: int) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Extract horizontal and vertical lines via morphology opening."""
    inv = 255 - bin_img
    h_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (h_ksize, 1))
    v_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, v_ksize))
    h_lines = cv2.morphologyEx(inv, cv2.MORPH_OPEN, h_kernel, iterations=1)
    v_lines = cv2.morphologyEx(inv, cv2.MORPH_OPEN, v_kernel, iterations=1)
    grid = cv2.bitwise_or(h_lines, v_lines)
    return h_lines, v_lines, grid


def find_largest_table_bbox(grid_mask: np.ndarray, img_shape: Tuple[int, int], margin_ratio: float = 0.02) -> Tuple[int,int,int,int]:
    """Find table bbox from grid mask by largest contour."""
    H, W = img_shape
    cnts, _ = cv2.findContours(grid_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    best = None
    best_area = 0
    mx = int(W * margin_ratio)
    my = int(H * margin_ratio)

    for c in cnts:
        x, y, w, h = cv2.boundingRect(c)
        area = w * h
        if area < best_area:
            continue
        if h < H * 0.2 or w < W * 0.3:
            continue
        best = (x, y, w, h)
        best_area = area

    if best is None:
        return (mx, my, W - 2*mx, H - 2*my)
    return best


def _cluster_positions(pos: np.ndarray, max_gap: int) -> List[int]:
    if len(pos) == 0:
        return []
    pos = np.sort(pos)
    clusters = []
    start = pos[0]
    prev = pos[0]
    for p in pos[1:]:
        if p - prev > max_gap:
            clusters.append((start, prev))
            start = p
        prev = p
    clusters.append((start, prev))
    return [int((a+b)//2) for a,b in clusters]


def _smooth_1d(x: np.ndarray, win: int) -> np.ndarray:
    if win <= 1:
        return x
    k = np.ones(win, dtype=np.float32) / win
    return np.convolve(x.astype(np.float32), k, mode="same")


def estimate_grid_lines(v_lines: np.ndarray, h_lines: np.ndarray, table_bbox: Tuple[int,int,int,int],
                        cols: Optional[int], rows: Optional[int]) -> Tuple[List[int], List[int]]:
    x0, y0, w, h = table_bbox
    v = v_lines[y0:y0+h, x0:x0+w]
    hh = h_lines[y0:y0+h, x0:x0+w]

    vproj = _smooth_1d(v.sum(axis=0), win=max(5, w//200))
    hproj = _smooth_1d(hh.sum(axis=1), win=max(5, h//200))

    vx = np.where(vproj > 0.5 * vproj.max())[0]
    hy = np.where(hproj > 0.5 * hproj.max())[0]

    xs = _cluster_positions(vx, max_gap=max(2, w//300))
    ys = _cluster_positions(hy, max_gap=max(2, h//300))

    def reconcile(vals: List[int], expected_lines: Optional[int], limit: int) -> List[int]:
        if expected_lines is None:
            return vals
        if expected_lines <= 1:
            return [0, limit-1]
        if len(vals) == expected_lines:
            return vals
        if len(vals) < expected_lines:
            return [int(round(i*(limit-1)/(expected_lines-1))) for i in range(expected_lines)]
        idxs = np.linspace(0, len(vals)-1, expected_lines).round().astype(int)
        return [vals[i] for i in idxs]

    xs = reconcile(sorted(set(xs)), (cols+1) if cols is not None else None, w)
    ys = reconcile(sorted(set(ys)), (rows+1) if rows is not None else None, h)

    return [x0 + x for x in xs], [y0 + y for y in ys]


def find_rect_roi_in_region(grid_mask: np.ndarray, region: Tuple[int,int,int,int],
                            min_w: int, min_h: int, max_w: int, max_h: int) -> Optional[Tuple[int,int,int,int]]:
    x0,y0,w,h = region
    sub = grid_mask[y0:y0+h, x0:x0+w]
    cnts, _ = cv2.findContours(sub, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    best = None
    best_area = 0
    for c in cnts:
        x,y,ww,hh = cv2.boundingRect(c)
        if ww < min_w or hh < min_h or ww > max_w or hh > max_h:
            continue
        area = ww*hh
        if area > best_area:
            best_area = area
            best = (x0+x, y0+y, ww, hh)
    return best


def fallback_box(W: int, H: int, rx: float, ry: float, rw: float, rh: float) -> Tuple[int,int,int,int]:
    return (int(W*rx), int(H*ry), int(W*rw), int(H*rh))


def build_template_json(
    facility_id: str,
    template_image_gcs_uri: str,
    page_w: int,
    page_h: int,
    facility_name_box: Tuple[int,int,int,int],
    menu_band: Tuple[int,int,int,int],
    notes_box: Tuple[int,int,int,int],
    qty_schema: dict,
    qty_boxes_row_major: List[Tuple[int,int,int,int]],
) -> dict:
    return {
        "facility_id": facility_id,
        "version": 1,
        "template_image_gcs_uri": template_image_gcs_uri,
        "match": {"orb_nfeatures": 2000, "min_matches": 25, "min_inlier_ratio": 0.15},
        "warp": {"output_size": [page_w, page_h]},
        "rois": {
            "facility_name_box": list(facility_name_box),
            "menu_band": list(menu_band),
            "qty": {"schema": qty_schema, "boxes_row_major": [list(b) for b in qty_boxes_row_major]},
            "notes_box": list(notes_box),
        },
        "postprocess": {
            "qty_regex": r"^\d{0,2}$",
            "normalize_fullwidth": True,
            "reject_repetition": {"max_repeat_run": 3, "min_unique_line_ratio": 0.3},
            "retry": {"max_attempts": 2, "crop_inset_px": [6, 6, 6, 6], "alt_binarize": True},
        },
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", required=True, type=Path)
    ap.add_argument("--facility", required=True, help="facility_id (e.g. FAC_A)")
    ap.add_argument("--template-id", required=True, help="template_id (for naming only)")
    ap.add_argument("--out", default="out", type=Path)
    ap.add_argument("--dpi", default=350, type=int)
    ap.add_argument("--rows", type=int, default=None)
    ap.add_argument("--cols", type=int, default=None)
    ap.add_argument("--gcs-uri", default="", help="template_image_gcs_uri placeholder")
    args = ap.parse_args()

    out_dir = args.out / args.facility
    ensure_dir(out_dir)

    img = run_pdftoppm_first_page(args.pdf, dpi=args.dpi)
    H, W = img.shape[:2]
    save_png(out_dir / "template.png", img)

    gray = to_gray(img)
    bin_img = otsu_bin(gray)

    h_ksize = max(30, W // 40)
    v_ksize = max(30, H // 60)

    h_lines, v_lines, grid = extract_lines(bin_img, h_ksize=h_ksize, v_ksize=v_ksize)

    debug_lines = cv2.cvtColor(255 - grid, cv2.COLOR_GRAY2BGR)
    save_png(out_dir / "debug_lines.png", debug_lines)

    table_bbox = find_largest_table_bbox(grid, (H, W))
    x0, y0, tw, th = table_bbox

    dbg = img.copy()
    cv2.rectangle(dbg, (x0, y0), (x0+tw, y0+th), (0,0,255), 3)
    save_png(out_dir / "debug_table_bbox.png", dbg)

    xs, ys = estimate_grid_lines(v_lines, h_lines, table_bbox, cols=args.cols, rows=args.rows)

    inset = 6
    xs = sorted(xs)
    ys = sorted(ys)

    qty_boxes = []
    for r in range(len(ys)-1):
        for c in range(len(xs)-1):
            xL, xR = xs[c], xs[c+1]
            yT, yB = ys[r], ys[r+1]
            x = xL + inset
            y = yT + inset
            w = max(1, (xR - xL) - 2*inset)
            h = max(1, (yB - yT) - 2*inset)
            qty_boxes.append((x,y,w,h))

    top_region = (0, 0, W, int(H*0.25))
    name_box = find_rect_roi_in_region(
        grid, top_region,
        min_w=int(W*0.15), min_h=int(H*0.02),
        max_w=int(W*0.70), max_h=int(H*0.12)
    ) or fallback_box(W,H, 0.06, 0.06, 0.45, 0.06)

    bottom_region = (0, int(H*0.70), W, int(H*0.30))
    notes_box = find_rect_roi_in_region(
        grid, bottom_region,
        min_w=int(W*0.50), min_h=int(H*0.06),
        max_w=int(W*0.95), max_h=int(H*0.35)
    ) or fallback_box(W,H, 0.05, 0.78, 0.90, 0.18)

    menu_band = (x0, y0, int(tw*0.45), th)

    qty_schema = {
        "rows": args.rows if args.rows is not None else max(0, len(ys)-1),
        "cols": args.cols if args.cols is not None else max(0, len(xs)-1),
        "row_names": [f"r{i}" for i in range(args.rows if args.rows else max(0, len(ys)-1))],
        "col_names": [f"c{j}" for j in range(args.cols if args.cols else max(0, len(xs)-1))],
    }

    tpl_json = build_template_json(
        facility_id=args.facility,
        template_image_gcs_uri=args.gcs_uri or "gs://YOUR_BUCKET/templates/REPLACE_ME.png",
        page_w=W, page_h=H,
        facility_name_box=name_box,
        menu_band=menu_band,
        notes_box=notes_box,
        qty_schema=qty_schema,
        qty_boxes_row_major=qty_boxes,
    )

    (out_dir / "template.json").write_text(json.dumps(tpl_json, ensure_ascii=False, indent=2), encoding="utf-8")

    dbg2 = img.copy()
    for x in xs:
        cv2.line(dbg2, (x, y0), (x, y0+th), (255,0,0), 2)
    for y in ys:
        cv2.line(dbg2, (x0, y), (x0+tw, y), (0,255,0), 2)
    cv2.rectangle(dbg2, (name_box[0], name_box[1]), (name_box[0]+name_box[2], name_box[1]+name_box[3]), (0,0,255), 2)
    cv2.rectangle(dbg2, (notes_box[0], notes_box[1]), (notes_box[0]+notes_box[2], notes_box[1]+notes_box[3]), (0,0,255), 2)
    save_png(out_dir / "debug_grid_overlay.png", dbg2)

    print(f"[OK] Wrote: {out_dir}/template.png, template.json, debug_*.png")


if __name__ == "__main__":
    main()
```

---

## 5. 生成後の登録手順（運用）

1) `template.png` を GCS にアップロード（例: `gs://YOUR_BUCKET/templates/<template_id>.png`）
2) `template.json` の `template_image_gcs_uri` をそのURIに置換
3) Firestore の `templates/{template_id}` に `template.json` を登録

---

## 6. 注意

- 自動推定は「初期値」です。`debug_grid_overlay.png` を見て、セルROIが正しく乗っているか確認してください。
- 施設ごとの“区分”が異なる場合、`--rows/--cols` と `menu_band` の比率（0.45など）をテンプレごとに調整してください。

