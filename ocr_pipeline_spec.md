# 施設別テンプレ対応・注文書OCRパイプライン（GCP / DeepSeek-OCR）仕様＋参照実装

このドキュメントは **Codex にそのまま渡して実装を進められる**ように、仕様と「動く」参照コード（Cloud Run前提）を同一Markdownにまとめたものです。

---

## 0. ゴール

- Cloud Storage に投入された **1ページPDF** の注文書を自動処理する
- **施設ごとに異なるフォーマット（区分）**をテンプレで吸収する
  - 同一施設の別週: 同一テンプレ再利用
  - 別施設: 別テンプレへ自動切替
- OCR は Vertex AI Model Garden MaaS の **DeepSeek-OCR** を利用する（ROI単位で呼ぶ）

---

## 1. アーキテクチャ

### 1.1 コンポーネント
- Cloud Storage
  - `input/` : PDF投入
  - `artifacts/` : 変換画像・デバッグ中間成果物（任意）
  - `output/` : 抽出結果 JSON
- Eventarc → Cloud Run
  - GCS finalizeイベントで Cloud Run を起動
- Firestore
  - `templates/` テンプレ定義（ROI・後処理ルール）
  - `facilities/` 施設→テンプレの運用管理（任意）
  - `jobs/` 重複実行防止・監査

---

## 2. データモデル（Firestore）

### 2.1 `templates/{template_id}`
```json
{
  "facility_id": "FAC_001",
  "version": 1,
  "template_image_gcs_uri": "gs://YOUR_BUCKET/templates/TPL_FAC_001_v1.png",
  "match": {
    "orb_nfeatures": 2000,
    "min_matches": 25,
    "min_inlier_ratio": 0.15
  },
  "warp": {
    "output_size": [2480, 3508]
  },
  "rois": {
    "facility_name_box": [140, 220, 900, 180],
    "menu_band": [120, 760, 900, 1900],
    "qty": {
      "schema": {
        "rows": 7,
        "cols": 6,
        "row_names": ["day1","day2","day3","day4","day5","day6","day7"],
        "col_names": ["normal_2f","normal_3f","soft_2f","soft_3f","mix_2f","mix_3f"]
      },
      "boxes_row_major": [
        [1200, 760, 140, 110],
        [1350, 760, 140, 110]
      ]
    },
    "notes_box": [120, 2800, 2200, 500]
  },
  "postprocess": {
    "qty_regex": "^\\d{0,2}$",
    "normalize_fullwidth": true,
    "reject_repetition": {
      "max_repeat_run": 3,
      "min_unique_line_ratio": 0.3
    },
    "retry": {
      "max_attempts": 2,
      "crop_inset_px": [6, 6, 6, 6],
      "alt_binarize": true
    }
  }
}
```

### 2.2 `jobs/{job_id}`
```json
{
  "status": "running",
  "input": {"bucket":"...", "name":"...", "generation":"..."},
  "template_id": "TPL_FAC_001_v1",
  "metrics": {"ocr_calls": 0, "retries": 0},
  "output": {"bucket":"...", "name":"..."},
  "error": null
}
```

---

## 3. 処理仕様（前処理→テンプレ判定→OCR→後処理）

### 3.1 PDF→画像化（必須）
- 350〜400dpiのPNGへ変換（1ページ）
- poppler `pdftoppm` を Cloud Run コンテナに同梱して実行

### 3.2 画像前処理（2系統）
- A系統（テンプレ判定/整列用）: 罫線保持 + 二値化
- B系統（OCR用）: 罫線を弱める/除去して文字を立てる

### 3.3 テンプレ判定（施設別フォーマット切替）
- ORB特徴点 + Homography(RANSAC)
- `min_matches`, `min_inlier_ratio` を満たすテンプレのうち、スコア最大を採用
- 閾値未満なら `unclassified` として保存し人手登録へ

### 3.4 位置合わせ（warp）
- テンプレ座標系にワープした画像を作成し、ROI座標を固定化

### 3.5 OCR（ROI単位）
- 施設名枠（任意）
- 献立帯（読むなら）
- 数量セル（最重要・セル単位）
- 備考欄（任意）

### 3.6 後処理（異常検知→セルだけ再試行）
- 数量セルは `qty_regex` で検証（数字以外混入・長文は失敗）
- 反復検知（同一行連続 / ユニーク行比率）
- 失敗セルのみ:
  - 罫線除去あり/なし画像の切替
  - crop inset（セル外周を削る）
  - alt二値化
- それでも失敗なら `failed_cells` に記録して人手確認へ

---

# 4. 参照実装（Cloud Run用）

> ここから下は「そのままリポジトリに置ける」コードです。

## 4.1 ディレクトリ構造
```text
ocr_pipeline/
  app/
    main.py
    pdf_render.py
    preprocess.py
    template_match.py
    rois.py
    postprocess.py
  Dockerfile
  requirements.txt
```

---

## 4.2 Dockerfile
```dockerfile
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends     poppler-utils     libgl1     libglib2.0-0  && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app

ENV PORT=8080
CMD ["gunicorn", "-b", ":8080", "-w", "1", "app.main:app"]
```

## 4.3 requirements.txt
```txt
flask==3.0.3
gunicorn==22.0.0
requests==2.32.3
google-auth==2.34.0
google-cloud-storage==2.18.2
google-cloud-firestore==2.17.0
opencv-python-headless==4.10.0.84
numpy==2.0.2
Pillow==10.4.0
```

---

## 4.4 app/main.py
```python
import os
import json
from flask import Flask, request, jsonify
from google.cloud import storage, firestore

from app.pdf_render import render_pdf_to_png_bytes
from app.preprocess import build_images_for_match_and_ocr
from app.template_match import choose_template_and_warp
from app.rois import load_template_config, crop_rois
from app.ocr_engine import ocr_roi_image
from app.postprocess import postprocess_and_retry

app = Flask(__name__)

PROJECT_ID = os.environ["GCP_PROJECT"]
OCR_ENGINE = os.environ.get("OCR_ENGINE", "yomitoku")

db = firestore.Client(project=PROJECT_ID)
gcs = storage.Client(project=PROJECT_ID)

def parse_gcs_event(payload: dict) -> tuple[str, str, str]:
    """
    Eventarc/GCS finalize の payload 差異を吸収して bucket/name/generation を抽出。
    """
    data = payload.get("data") or payload
    bucket = data.get("bucket") or data.get("bucketId")
    name = data.get("name") or data.get("object") or data.get("objectId")
    generation = str(data.get("generation") or data.get("metageneration") or "")
    if not bucket or not name:
        raise ValueError(f"Invalid event payload keys={list((data or {}).keys())}")
    return bucket, name, generation

@app.post("/")
def handler():
    event = request.get_json(force=True, silent=False)
    bucket, name, generation = parse_gcs_event(event)

    job_id = f"{bucket}:{name}:{generation}"
    job_ref = db.collection("jobs").document(job_id)
    if job_ref.get().exists:
        return jsonify({"status": "duplicate", "job_id": job_id}), 200
    job_ref.set({"status": "running", "input": {"bucket": bucket, "name": name, "generation": generation}})

    try:
        pdf_bytes = gcs.bucket(bucket).blob(name).download_as_bytes()

        page_png = render_pdf_to_png_bytes(pdf_bytes, dpi=350)

        img_match, img_ocr = build_images_for_match_and_ocr(page_png)

        template_id, warped_match, warped_ocr = choose_template_and_warp(db, img_match, img_ocr)

        tpl_cfg = load_template_config(db, template_id)
        rois = crop_rois(warped_ocr, tpl_cfg)

        result = postprocess_and_retry(
            rois=rois,
            tpl_cfg=tpl_cfg,
            ocr_fn=lambda img, prompt, max_tokens: ocr_roi_image(
                region=REGION,
                project_id=PROJECT_ID,
                model=MODEL,
                image_bgr=img,
                prompt=prompt,
                max_tokens=max_tokens,
            ),
        )

        out_name = f"output/{os.path.basename(name)}.json"
        gcs.bucket(bucket).blob(out_name).upload_from_string(
            json.dumps(result, ensure_ascii=False),
            content_type="application/json; charset=utf-8",
        )

        job_ref.update({"status": "done", "template_id": template_id, "output": {"bucket": bucket, "name": out_name}})
        return jsonify({"status": "done", "job_id": job_id, "template_id": template_id, "output": f"gs://{bucket}/{out_name}"}), 200

    except Exception as e:
        job_ref.update({"status": "failed", "error": repr(e)})
        raise
```

---

## 4.5 app/pdf_render.py（pdftoppm）
```python
import subprocess
import tempfile
from pathlib import Path

def render_pdf_to_png_bytes(pdf_bytes: bytes, dpi: int = 350) -> bytes:
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        pdf_path = d / "in.pdf"
        pdf_path.write_bytes(pdf_bytes)

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
        return png_path.read_bytes()
```

---

## 4.6 app/preprocess.py（2系統画像）
```python
import cv2
import numpy as np

def build_images_for_match_and_ocr(png_bytes: bytes):
    n = np.frombuffer(png_bytes, dtype=np.uint8)
    img = cv2.imdecode(n, cv2.IMREAD_COLOR)

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    den = cv2.fastNlMeansDenoising(gray, None, h=10, templateWindowSize=7, searchWindowSize=21)

    _, bin_ = cv2.threshold(den, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    match = cv2.cvtColor(bin_, cv2.COLOR_GRAY2BGR)

    ocr = _remove_lines_for_ocr(bin_)
    ocr = cv2.cvtColor(ocr, cv2.COLOR_GRAY2BGR)

    return match, ocr

def _remove_lines_for_ocr(bin_img):
    img = bin_img.copy()

    h_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (40, 1))
    h_lines = cv2.morphologyEx(img, cv2.MORPH_OPEN, h_kernel, iterations=1)

    v_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, 40))
    v_lines = cv2.morphologyEx(img, cv2.MORPH_OPEN, v_kernel, iterations=1)

    lines = cv2.bitwise_or(h_lines, v_lines)

    cleaned = img.copy()
    cleaned[lines > 0] = 255
    return cleaned
```

---

## 4.7 app/template_match.py（テンプレ判定＋warp）
```python
import cv2
import numpy as np
from google.cloud import storage

gcs = storage.Client()

def _download_template_png(uri: str) -> np.ndarray:
    assert uri.startswith("gs://")
    _, rest = uri.split("gs://", 1)
    bucket, path = rest.split("/", 1)
    data = gcs.bucket(bucket).blob(path).download_as_bytes()
    n = np.frombuffer(data, dtype=np.uint8)
    return cv2.imdecode(n, cv2.IMREAD_COLOR)

def choose_template_and_warp(db, img_match_bgr, img_ocr_bgr):
    templates = list(db.collection("templates").stream())
    if not templates:
        raise RuntimeError("No templates registered: templates/")

    best_id = None
    best_score = -1
    best_warp = None

    orb = cv2.ORB_create(nfeatures=2000)
    bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=False)

    kp1, des1 = orb.detectAndCompute(cv2.cvtColor(img_match_bgr, cv2.COLOR_BGR2GRAY), None)

    for doc in templates:
        cfg = doc.to_dict()
        tpl_img = _download_template_png(cfg["template_image_gcs_uri"])
        kp2, des2 = orb.detectAndCompute(cv2.cvtColor(tpl_img, cv2.COLOR_BGR2GRAY), None)

        if des1 is None or des2 is None:
            continue

        matches = bf.knnMatch(des1, des2, k=2)
        good = []
        for m, n in matches:
            if m.distance < 0.75 * n.distance:
                good.append(m)

        min_matches = cfg.get("match", {}).get("min_matches", 25)
        if len(good) < min_matches:
            continue

        src = np.float32([kp1[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
        dst = np.float32([kp2[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)

        H, mask = cv2.findHomography(src, dst, cv2.RANSAC, 5.0)
        if H is None or mask is None:
            continue

        inliers = int(mask.sum())
        inlier_ratio = inliers / max(1, len(good))
        min_inlier_ratio = cfg.get("match", {}).get("min_inlier_ratio", 0.15)
        if inlier_ratio < min_inlier_ratio:
            continue

        score = inlier_ratio * 1000 + inliers
        if score > best_score:
            W, Hh = cfg.get("warp", {}).get("output_size", [tpl_img.shape[1], tpl_img.shape[0]])
            warped_match = cv2.warpPerspective(img_match_bgr, H, (W, Hh))
            warped_ocr = cv2.warpPerspective(img_ocr_bgr, H, (W, Hh))
            best_score = score
            best_id = doc.id
            best_warp = (warped_match, warped_ocr)

    if best_id is None:
        raise RuntimeError("Template classification failed (no template above thresholds).")

    return best_id, best_warp[0], best_warp[1]
```

---

## 4.8 app/rois.py（テンプレ設定ロード＋ROI切り出し）
```python
def load_template_config(db, template_id: str) -> dict:
    doc = db.collection("templates").document(template_id).get()
    if not doc.exists:
        raise RuntimeError(f"Template not found: {template_id}")
    cfg = doc.to_dict()
    cfg["id"] = template_id
    return cfg

def _crop(img_bgr, box):
    x, y, w, h = box
    return img_bgr[y:y+h, x:x+w].copy()

def crop_rois(warped_ocr_bgr, tpl_cfg: dict) -> dict:
    rois = {}
    r = tpl_cfg["rois"]

    if "facility_name_box" in r:
        rois["facility_name"] = _crop(warped_ocr_bgr, r["facility_name_box"])

    if "menu_band" in r:
        rois["menu_band"] = _crop(warped_ocr_bgr, r["menu_band"])

    qty = r.get("qty")
    if qty:
        boxes = qty["boxes_row_major"]
        rois["qty_cells"] = [_crop(warped_ocr_bgr, b) for b in boxes]
        rois["qty_schema"] = qty["schema"]

    if "notes_box" in r:
        rois["notes"] = _crop(warped_ocr_bgr, r["notes_box"])

    return rois
```

---

## 4.9 app/ocr_engine.py（OCR実装はyomitoku-ocr仕様で更新）
ここは次の仕様書で差し替える。`ocr_roi_image(...)` のI/Fだけ維持し、OCR実装は別途定義する。

---

## 4.10 app/postprocess.py（検証＋セル再試行）
```python
import re

def _dedup_consecutive_lines(text: str) -> str:
    out = []
    prev = None
    for line in text.splitlines():
        if line != prev:
            out.append(line)
        prev = line
    return "
".join(out).strip()

def _repeat_run_max(text: str) -> int:
    max_run = 1
    run = 1
    prev = None
    for line in text.splitlines():
        if line and line == prev:
            run += 1
            max_run = max(max_run, run)
        else:
            run = 1
        prev = line
    return max_run

def postprocess_and_retry(*, rois: dict, tpl_cfg: dict, ocr_fn):
    pp = tpl_cfg.get("postprocess", {})
    qty_re = re.compile(pp.get("qty_regex", r"^\d{0,2}$"))

    retry_cfg = pp.get("retry", {})
    max_attempts = int(retry_cfg.get("max_attempts", 2))
    max_repeat_run = int(pp.get("reject_repetition", {}).get("max_repeat_run", 3))

    result = {
        "template_id": tpl_cfg.get("id"),
        "qty": {},
        "failed_cells": [],
    }

    if "qty_cells" in rois:
        schema = rois.get("qty_schema", {})
        rows = schema.get("rows", 0)
        cols = schema.get("cols", 0)
        col_names = schema.get("col_names", [str(i) for i in range(cols)])
        row_names = schema.get("row_names", [str(i) for i in range(rows)])

        cells = rois["qty_cells"]
        for idx, cell_img in enumerate(cells):
            r_i = idx // max(1, cols)
            c_i = idx % max(1, cols)

            row_key = row_names[r_i] if r_i < len(row_names) else str(r_i)
            col_key = col_names[c_i] if c_i < len(col_names) else str(c_i)

            prompt = "画像内の数量（数字）だけを返してください。推測は禁止。数字が無ければ空で返してください。出力は数字のみ。"
            parsed = None
            last_raw = ""

            for _ in range(max_attempts):
                raw = ocr_fn(cell_img, prompt, 32)
                raw = _dedup_consecutive_lines(raw).strip()
                last_raw = raw

                if _repeat_run_max(raw) > max_repeat_run:
                    continue

                raw2 = raw.replace("０","0").replace("１","1").replace("２","2").replace("３","3").replace("４","4")                           .replace("５","5").replace("６","6").replace("７","7").replace("８","8").replace("９","9")

                if raw2 == "":
                    parsed = None
                    break
                if qty_re.match(raw2):
                    parsed = int(raw2)
                    break

            if parsed is None and last_raw not in ("", None):
                result["failed_cells"].append({"row": row_key, "col": col_key, "raw": last_raw})

            result["qty"].setdefault(row_key, {})[col_key] = parsed

    if "notes" in rois:
        prompt = "画像内の文章を見えたまま転記してください。推測は禁止。"
        notes = ocr_fn(rois["notes"], prompt, 512)
        result["notes"] = _dedup_consecutive_lines(notes)

    return result
```

---

# 5. デプロイ（gcloudコマンド例）

```bash
# Cloud Run デプロイ
gcloud run deploy ocr-pipeline   --source .   --region asia-northeast1   --set-env-vars GCP_PROJECT=YOUR_PROJECT,OCR_ENGINE=yomitoku   --allow-unauthenticated

# Eventarc トリガ（GCS finalize → Cloud Run）
gcloud eventarc triggers create ocr-pipeline-gcs-finalize   --location asia-northeast1   --destination-run-service ocr-pipeline   --destination-run-region asia-northeast1   --event-filters="type=google.cloud.storage.object.v1.finalized"   --event-filters="bucket=YOUR_BUCKET"   --service-account YOUR_EVENTARC_SA@YOUR_PROJECT.iam.gserviceaccount.com
```

---

# 6. Codexへの指示（実装タスク分割）

- Task 1: `templates/` のFirestoreデータを前提に end-to-end（PDF→JSON）を通す
- Task 2: `unclassified`（テンプレ判定失敗）を `artifacts/` に保存し、Firestoreにキュー登録
- Task 3: ROI登録UI（必要なら別スプリント）
