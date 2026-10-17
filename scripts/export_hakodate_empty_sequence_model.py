"""Export the exact approved learned EOS row; no training or model adjustment."""
import argparse
from io import BytesIO
import json
from pathlib import Path
import sys

import torch
from safetensors.torch import save

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from src.services.hakodate_empty_sequence_model import (
    ARTIFACT_DIR, BASE_SHA256, CONTRACT, LEARNED_SHA256, TRAINING_SOURCE_SHA256, file_sha256,
)


def export(learned_path, training_source, output_dir=ARTIFACT_DIR):
    if Path(output_dir).resolve() != ARTIFACT_DIR.resolve():
        raise ValueError("export_target_must_be_owned_model_directory")
    if file_sha256(learned_path) != LEARNED_SHA256 or file_sha256(training_source) != TRAINING_SOURCE_SHA256:
        raise ValueError("learned_source_integrity")
    learned = torch.load(BytesIO(Path(learned_path).read_bytes()), map_location="cpu", weights_only=True)
    if set(learned) != {"eos_weight", "eos_bias", "base_sha256"} or learned["base_sha256"] != BASE_SHA256:
        raise ValueError("learned_source_contract")
    tensors = {key: learned[key].detach().contiguous() for key in ("eos_weight", "eos_bias")}
    if (tensors["eos_weight"].shape != (512,) or tensors["eos_bias"].shape != ()
            or any(t.dtype != torch.float32 or not torch.isfinite(t).all() for t in tensors.values())):
        raise ValueError("learned_source_shape")
    artifact = save(tensors, metadata={"contract": CONTRACT})
    from hashlib import sha256
    provenance = {"contract": CONTRACT, "base_sha256": BASE_SHA256,
        "adapter_sha256": sha256(artifact).hexdigest(), "learned_source_sha256": LEARNED_SHA256,
        "training_source_sha256": TRAINING_SOURCE_SHA256, "native_version": "0.9.5",
        "architecture": "parseqv2/refine_iters=1", "changed_logits": "initial EOS only",
        "training": {"images": 384, "seed": 1742, "source": "synthetic only; no FAX training",
                     "fonts": ["Arial.ttf", "Courier New.ttf", "Arial Narrow.ttf"]}}
    output_dir.mkdir(parents=True, exist_ok=True)
    for name, content in {"adapter.safetensors": artifact,
                          "provenance.json": (json.dumps(provenance, sort_keys=True, indent=2) + "\n").encode()}.items():
        target = output_dir / name
        if target.exists():
            if target.read_bytes() != content:
                raise ValueError("export_would_replace_different_artifact")
        else:
            target.write_bytes(content)
        print(name, len(content), sha256(content).hexdigest())


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--learned-eos", type=Path, required=True)
    parser.add_argument("--training-source", type=Path, required=True)
    args = parser.parse_args()
    export(args.learned_eos, args.training_source)
