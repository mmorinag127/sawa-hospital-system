#!/usr/bin/env python3
from __future__ import annotations

import importlib.metadata as metadata


def main() -> None:
    blocked: list[str] = []
    for dist in metadata.distributions():
        name = str(dist.metadata["Name"] or "").strip()
        normalized = name.lower()
        if normalized.startswith(("nvidia-", "cuda-")) or normalized == "triton":
            blocked.append(name)
    if blocked:
        raise SystemExit(f"GPU/CUDA packages must not be installed: {blocked}")


if __name__ == "__main__":
    main()
