"""Convert the validated native workbook without changing its content."""
from io import BytesIO
from pathlib import Path
import re
import subprocess
from tempfile import TemporaryDirectory
from threading import BoundedSemaphore
from typing import Literal
from urllib.parse import quote

from fastapi import HTTPException
from starlette.responses import Response

OutputFormat = Literal["xlsx", "pdf"]
# ponytail: one conversion per single-process API instance; multiple workers require a shared limit.
_pdf_slot = BoundedSemaphore(1)


def workbook_pdf(workbook: bytes) -> bytes:
    if not _pdf_slot.acquire(blocking=False):
        raise HTTPException(503, "別のPDFを作成中です。少し待って再実行してください。", headers={"Retry-After": "10"})
    try:
        return _convert_workbook_pdf(workbook)
    finally:
        _pdf_slot.release()


def _convert_workbook_pdf(workbook: bytes) -> bytes:
    with TemporaryDirectory(prefix="lunch-pdf-") as directory:
        root = Path(directory)
        source = root / "workbook.xlsx"
        source.write_bytes(workbook)
        try:
            subprocess.run([
                "soffice", f"-env:UserInstallation={(root / 'profile').as_uri()}",
                "--headless", "--convert-to", "pdf:calc_pdf_Export",
                "--outdir", str(root), str(source),
            ], check=True, timeout=120, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            data = (root / "workbook.pdf").read_bytes()
        except subprocess.TimeoutExpired:
            raise HTTPException(504, "PDF変換が時間内に完了しませんでした。再実行してください。") from None
        except (OSError, subprocess.CalledProcessError):
            raise HTTPException(503, "PDF変換に失敗しました。管理者にお問い合わせください。") from None
        if not data.startswith(b"%PDF-") or not data.rstrip().endswith(b"%%EOF"):
            raise HTTPException(503, "PDF変換結果を確認できませんでした。")
        # A successful converter exit does not prove a complete xref/trailer/page tree.
        try:
            inspected = subprocess.run(["pdfinfo", str(root / "workbook.pdf")], check=True,
                timeout=15, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            raise HTTPException(503, "PDFの構造検証に失敗しました。再実行してください。") from None
        if not re.search(rb"^Pages:\s+[1-9][0-9]*\s*$", inspected.stdout, re.MULTILINE):
            raise HTTPException(503, "PDFのページを確認できませんでした。")
        return data


def workbook_response(workbook: bytes | BytesIO, filename: str, output: OutputFormat):
    data = workbook.getvalue() if isinstance(workbook, BytesIO) else workbook
    if output == "pdf":
        data = workbook_pdf(data)
    # Completed bytes: Content-Length makes transport truncation detectable by clients.
    return Response(content=data, media_type="application/pdf" if output == "pdf" else
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename + '.' + output)}",
                 "Cache-Control": "private, no-store"})
