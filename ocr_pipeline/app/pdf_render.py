import subprocess
import tempfile
from pathlib import Path


def render_pdf_to_png_bytes(pdf_bytes: bytes, dpi: int = 350) -> bytes:
    with tempfile.TemporaryDirectory() as workdir:
        base = Path(workdir)
        pdf_path = base / "input.pdf"
        pdf_path.write_bytes(pdf_bytes)

        out_prefix = base / "page"
        cmd = [
            "pdftoppm",
            "-f",
            "1",
            "-l",
            "1",
            "-png",
            "-rx",
            str(dpi),
            "-ry",
            str(dpi),
            str(pdf_path),
            str(out_prefix),
        ]
        subprocess.check_call(cmd)

        png_path = base / "page-1.png"
        return png_path.read_bytes()
