"""Read-only diagnosis of a failed staging contact-sheet replay, not acceptance."""

import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import sys

import cv2
import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from src.services.hakodate_preprocess_observation import geometry_hash, pixels
from src.services.hakodate_recognizer_contact_sheet import build_recognizer_contact_sheet


def checked_bytes(path, receipt):
    data = path.read_bytes()
    if len(data) != receipt["size_bytes"] or hashlib.sha256(data).hexdigest() != receipt["sha256"]:
        raise ValueError("diagnostic_saved_artifact_changed")
    return data


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--observation", type=Path, required=True)
    parser.add_argument("--checkpoint-image", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = json.loads((args.artifact_dir / "ocr-payload.json").read_bytes())
    packet = payload["hakodate_canonical_pipeline"]["artifact_packet"]["outputs"]
    observation_bytes = checked_bytes(args.observation, packet["preprocess_observation"])
    if hashlib.sha256(observation_bytes).hexdigest() != "5a5bccf8e5c71d309fd99619786a1fff7fb9f6b6054768ccebe88a87a67a06cd":
        raise ValueError("diagnostic_case_changed")
    observation = json.loads(observation_bytes)
    checkpoint = observation["checkpoint"]
    image_bytes = checked_bytes(args.checkpoint_image, checkpoint["image"])
    records = json.loads(checked_bytes(args.artifact_dir / "recognizer-records.json", packet["records"]))
    checked_bytes(args.artifact_dir / "recognizer-contact.png", packet["contact_sheet"])
    source = cv2.imdecode(np.frombuffer(image_bytes, np.uint8), cv2.IMREAD_COLOR)
    source_pixels = pixels(source) if source is not None else None
    if source_pixels is None or source_pixels["sha256"] != "020e780f6f52a8b3f64566aef5dda8667a46118b4b839f9945ad36dd611a6a54":
        raise ValueError("diagnostic_image_source_changed")
    if (len(records) != 448 or len(checkpoint["regions"]) != 448
            or observation["observations"] != [row["recognizer_preprocess_observation"] for row in records]):
        raise ValueError("diagnostic_observation_identity_changed")
    for region, record in zip(checkpoint["regions"], records):
        saved = record["recognizer_preprocess_observation"]
        if (region["sheet_cell"] != record["sheet_cell"]
                or geometry_hash(region) != saved["geometry_sha256"]
                or saved["source_sha256"] != source_pixels["sha256"]):
            raise ValueError("diagnostic_geometry_source_changed")
    rebuilt, regions, _, skipped = build_recognizer_contact_sheet(
        rectified_fax_bgr=source, regions=checkpoint["regions"], line_mask=None, mode="corner_cc")
    with Image.open(args.artifact_dir / "recognizer-contact.png") as image:
        saved_pixels = np.asarray(image.convert("RGB"))
    rebuilt_pixels = np.asarray(rebuilt)
    if skipped or saved_pixels.shape != rebuilt_pixels.shape or len(regions) != len(records):
        raise ValueError("diagnostic_replay_structure_changed")
    changes = []
    for record, region in zip(records, regions):
        saved_observation = record["recognizer_preprocess_observation"]
        rebuilt_observation = region["recognizer_preprocess_observation"]
        if saved_observation != rebuilt_observation:
            changes.append({"sheet_cell": record["sheet_cell"], "changed_fields": [
                key for key in saved_observation if saved_observation[key] != rebuilt_observation.get(key)],
                "saved_warp": saved_observation["actual_warp"],
                "rebuilt_warp": rebuilt_observation["actual_warp"]})
    result = {
        "scope": "failed-staging-artifact diagnostic only; no OCR or acceptance",
        "source_run": 39031990552,
        "source_sha": "b1a1197850b35e04aa7b88365d342e4f0acfd970",
        "python": sys.version,
        "dependencies": {name: importlib.metadata.version(name) for name in ("numpy", "Pillow")},
        "opencv": cv2.__version__,
        "opencv_build": cv2.getBuildInformation(),
        "source_pixels": source_pixels,
        "contact_shape": list(saved_pixels.shape),
        "changed_contact_pixels": int(np.any(saved_pixels != rebuilt_pixels, axis=2).sum()),
        "observation_change_count": len(changes),
        "first_observation_changes": changes[:5],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({key: result[key] for key in (
        "scope", "opencv", "dependencies", "changed_contact_pixels", "observation_change_count")}))


if __name__ == "__main__":
    main()
