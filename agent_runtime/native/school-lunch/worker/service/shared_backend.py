from __future__ import annotations

import sys
from pathlib import Path


def ensure_backend_path() -> None:
    backend_root = Path(__file__).resolve().parents[2] / "backend"
    backend_root_str = str(backend_root)
    if backend_root_str not in sys.path:
        sys.path.insert(0, backend_root_str)
