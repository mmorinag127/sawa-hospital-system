"""Read the registered OCR overlay from the same trial copy as the reviewed PDF."""
import hashlib
from pathlib import Path
import re

from .store import Conflict


def render_original_page(data, page_number):
    import pymupdf

    try:
        with pymupdf.open(stream=data, filetype="pdf") as pdf:
            if not 1 <= page_number <= len(pdf):
                raise Conflict("原本PDFに指定のページがありません")
            page = pdf[page_number - 1]
            scale = min(2, 1600 / max(page.rect.width, page.rect.height))
            return page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False).tobytes("png"), len(pdf)
    except (RuntimeError, ValueError, ZeroDivisionError):
        raise Conflict("原本PDFを表示できません") from None


def review_evidence_id(review):
    sheet = review["sheet"]
    return review.get("selected_ocr_result_id") or sheet.get("selected_ocr_result_id") or sheet.get("base_evidence_run_id")


def read_overlay(data_root, item, evidence):
    snapshot = item["snapshot"]
    review = item["review"]
    order_id = snapshot.get("order_id")
    evidence_id = review_evidence_id(review)
    manifest = evidence.get("artifact_manifest_json") or {}
    packet = manifest.get("artifact_packet") or {}
    source = (packet.get("source_receipt") or {}).get("selected_pdf_receipt") or {}
    if (not evidence_id or evidence.get("id") != evidence_id or evidence.get("order_id") != order_id
            or packet.get("order_id") != order_id or source.get("order_id") != order_id
            or not snapshot.get("document_id") or source.get("document_id") != snapshot["document_id"]
            or source.get("uploaded_pdf_id") != item["fax_id"]
            or not snapshot.get("sha256") or source.get("sha256") != snapshot["sha256"]
            or review["original"].get("sha256") != snapshot["sha256"]):
        raise Conflict("原本とOCR画像の組み合わせを確認できません")
    receipt = (packet.get("outputs") or {}).get("overlay_png") or {}
    uri, sha, size = (receipt.get(key) for key in ("uri", "sha256", "size_bytes"))
    if (receipt.get("content_type") != "image/png" or not isinstance(uri, str)
            or not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{64}", sha)
            or type(size) is not int or not 0 < size <= 16 * 1024 * 1024):
        raise Conflict("このOCR結果の重ね表示画像はありません")
    relative = Path(uri)
    if relative.is_absolute() or relative.parts[:2] != ("ocr-artifacts", "artifacts") or ".." in relative.parts:
        raise Conflict("試用コピー内にOCR画像がありません")
    root = Path(data_root).resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root / "ocr-artifacts" / "artifacts"):
        raise Conflict("試用コピー内にOCR画像がありません")
    try:
        with path.open("rb") as file:
            data = file.read(size + 1)
    except OSError:
        raise Conflict("試用コピー内にOCR画像がありません") from None
    if len(data) != size or not data.startswith(b"\x89PNG\r\n\x1a\n") or hashlib.sha256(data).hexdigest() != sha:
        raise Conflict("OCR画像が登録時の内容と一致しません")
    return data
