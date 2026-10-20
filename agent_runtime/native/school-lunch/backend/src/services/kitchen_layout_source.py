"""Render only the operator-selected PDF page, with bounded native Poppler."""
from pathlib import Path
from tempfile import TemporaryDirectory
import subprocess


def render_pdf_page(content: bytes, page: int | None) -> bytes:
    if type(page) is not int or not 1 <= page <= 100:
        raise ValueError("PDFのページを1〜100で明示してください。ご飯用・パン用を原本で確認してください")
    if not content.startswith(b"%PDF-"):
        raise ValueError("有効なPDFを指定してください")
    with TemporaryDirectory(prefix="kitchen-layout-") as directory:
        source = Path(directory) / "source.pdf"
        target = Path(directory) / "page"
        source.write_bytes(content)
        try:
            subprocess.run(["pdftoppm", "-f", str(page), "-l", str(page), "-singlefile", "-scale-to", "2000", "-png", str(source), str(target)],
                           check=True, capture_output=True, timeout=30)
            return target.with_suffix(".png").read_bytes()
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
            raise ValueError("指定したPDFページを読み取れません。ページ番号・PDFを確認してください") from exc
