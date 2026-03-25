from pathlib import Path
from urllib.parse import urlparse

import cv2
import numpy as np

from app.rois import _load_template_config_from_registry, _load_template_registry, default_template_collection


def _resolve_local_template_path(path: Path) -> Path:
    if path.exists():
        return path
    text = path.as_posix()
    marker = "/src/data/"
    if marker in text:
        suffix = text.split(marker, 1)[1]
        local_fallback = Path(__file__).resolve().parents[1] / "src" / "data" / suffix
        if local_fallback.exists():
            return local_fallback
        backend_fallback = Path(__file__).resolve().parents[2] / "backend" / "src" / "data" / suffix
        if backend_fallback.exists():
            return backend_fallback
    return path


def _template_sources_from_registry(template_ids: list[str] | None) -> list[tuple[str, dict]]:
    sources: list[tuple[str, dict]] = []
    if template_ids:
        ids = [template_id for template_id in template_ids if template_id]
        for template_id in ids:
            cfg = _load_template_config_from_registry(template_id)
            if isinstance(cfg, dict):
                sources.append((template_id, cfg))
        return sources
    for template_id, cfg in _load_template_registry().items():
        if not isinstance(cfg, dict):
            continue
        sources.append((str(template_id), dict(cfg)))
    return sources


def _template_sources(db, collection: str, template_ids: list[str] | None) -> list[tuple[str, dict]]:
    sources: list[tuple[str, dict]] = []
    seen: set[str] = set()
    if template_ids:
        docs = [
            db.collection(collection).document(template_id).get()
            for template_id in template_ids
        ]
    else:
        docs = list(db.collection(collection).stream())
    for doc in docs:
        if not getattr(doc, "exists", False):
            continue
        cfg = doc.to_dict() or {}
        template_id = str(getattr(doc, "id", "") or cfg.get("id") or "").strip()
        if not template_id or template_id in seen:
            continue
        sources.append((template_id, cfg))
        seen.add(template_id)
    for template_id, cfg in _template_sources_from_registry(template_ids):
        if template_id in seen:
            continue
        sources.append((template_id, cfg))
        seen.add(template_id)
    return sources


def _download_template_png(uri: str) -> np.ndarray:
    if not uri:
        raise ValueError("template image uri is required")
    parsed = urlparse(uri)
    if parsed.scheme == "gs":
        from google.cloud import storage

        bucket = parsed.netloc
        path = parsed.path.lstrip("/")
        if not bucket or not path:
            raise ValueError(f"invalid gs uri: {uri}")
        data = storage.Client().bucket(bucket).blob(path).download_as_bytes()
    elif parsed.scheme in {"", "file"}:
        path = _resolve_local_template_path(Path(parsed.path if parsed.scheme else uri))
        data = path.read_bytes()
    else:
        raise ValueError(f"unsupported template image uri: {uri}")
    n = np.frombuffer(data, dtype=np.uint8)
    image = cv2.imdecode(n, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"failed to decode template image: {uri}")
    return image


def choose_template_and_warp(
    db,
    img_match_bgr,
    img_ocr_bgr,
    img_alt_bgr=None,
    collection: str | None = None,
    template_ids: list[str] | None = None,
):
    templates = _template_sources(db, collection or default_template_collection(), template_ids)
    if not templates:
        raise RuntimeError("No templates registered")

    best_id = None
    best_score = -1.0
    best_warp = None
    candidates = []

    orb_cache: dict[int, tuple[cv2.ORB, list, np.ndarray | None]] = {}
    bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=False)
    gray_input = cv2.cvtColor(img_match_bgr, cv2.COLOR_BGR2GRAY)

    for template_id, cfg in templates:
        uri = cfg.get("template_image_gcs_uri") or cfg.get("template_image_uri")
        if not uri:
            continue
        tpl_img = _download_template_png(uri)
        gray_tpl = cv2.cvtColor(tpl_img, cv2.COLOR_BGR2GRAY)

        match_cfg = cfg.get("match") if isinstance(cfg.get("match"), dict) else {}
        orb_nfeatures = int(match_cfg.get("orb_nfeatures", 2000))
        if orb_nfeatures not in orb_cache:
            orb = cv2.ORB_create(nfeatures=orb_nfeatures)
            kp1, des1 = orb.detectAndCompute(gray_input, None)
            orb_cache[orb_nfeatures] = (orb, kp1, des1)
        orb, kp1, des1 = orb_cache[orb_nfeatures]

        kp2, des2 = orb.detectAndCompute(gray_tpl, None)
        if des1 is None or des2 is None:
            candidates.append({"id": template_id, "status": "no_descriptors"})
            continue

        matches = bf.knnMatch(des1, des2, k=2)
        good = []
        for m, n in matches:
            if m.distance < 0.75 * n.distance:
                good.append(m)

        min_matches = int(match_cfg.get("min_matches", 25))
        if len(good) < min_matches:
            candidates.append(
                {"id": template_id, "status": "below_min_matches", "matches": len(good)}
            )
            continue

        src = np.float32([kp1[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
        dst = np.float32([kp2[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
        H, mask = cv2.findHomography(src, dst, cv2.RANSAC, 5.0)
        if H is None or mask is None:
            candidates.append({"id": template_id, "status": "homography_failed"})
            continue

        inliers = int(mask.sum())
        inlier_ratio = inliers / max(1, len(good))
        min_inlier_ratio = float(match_cfg.get("min_inlier_ratio", 0.15))
        if inlier_ratio < min_inlier_ratio:
            candidates.append(
                {
                    "id": template_id,
                    "status": "below_min_inlier_ratio",
                    "inlier_ratio": inlier_ratio,
                }
            )
            continue

        score = inlier_ratio * 1000 + inliers
        candidates.append(
            {
                "id": template_id,
                "status": "matched",
                "score": score,
                "matches": len(good),
                "inliers": inliers,
                "inlier_ratio": inlier_ratio,
            }
        )
        if score > best_score:
            output_size = cfg.get("warp", {}).get("output_size")
            if output_size and len(output_size) == 2:
                width, height = int(output_size[0]), int(output_size[1])
            else:
                height, width = tpl_img.shape[:2]
            warped_match = cv2.warpPerspective(img_match_bgr, H, (width, height))
            warped_ocr = cv2.warpPerspective(img_ocr_bgr, H, (width, height))
            warped_alt = None
            if img_alt_bgr is not None:
                warped_alt = cv2.warpPerspective(img_alt_bgr, H, (width, height))
            best_id = template_id
            best_score = score
            best_warp = (warped_match, warped_ocr, warped_alt)

    diagnostics = {"candidates": candidates}
    if best_id is None or best_warp is None:
        return None, None, None, None, diagnostics

    return best_id, best_warp[0], best_warp[1], best_warp[2], diagnostics
