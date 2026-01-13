import cv2
import numpy as np
from google.cloud import storage


gcs = storage.Client()


def _download_template_png(uri: str) -> np.ndarray:
    if not uri or not uri.startswith("gs://"):
        raise ValueError("template_image_gcs_uri must be gs://")
    _, rest = uri.split("gs://", 1)
    bucket, path = rest.split("/", 1)
    data = gcs.bucket(bucket).blob(path).download_as_bytes()
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
    collection: str = "templates",
    template_ids: list[str] | None = None,
):
    if template_ids:
        templates = [
            db.collection(collection).document(template_id).get()
            for template_id in template_ids
        ]
    else:
        templates = list(db.collection(collection).stream())
    if not templates:
        raise RuntimeError("No templates registered")

    best_id = None
    best_score = -1.0
    best_warp = None
    candidates = []

    orb_cache: dict[int, tuple[cv2.ORB, list, np.ndarray | None]] = {}
    bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=False)
    gray_input = cv2.cvtColor(img_match_bgr, cv2.COLOR_BGR2GRAY)

    for doc in templates:
        if not doc.exists:
            continue
        cfg = doc.to_dict() or {}
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
            candidates.append({"id": doc.id, "status": "no_descriptors"})
            continue

        matches = bf.knnMatch(des1, des2, k=2)
        good = []
        for m, n in matches:
            if m.distance < 0.75 * n.distance:
                good.append(m)

        min_matches = int(match_cfg.get("min_matches", 25))
        if len(good) < min_matches:
            candidates.append(
                {"id": doc.id, "status": "below_min_matches", "matches": len(good)}
            )
            continue

        src = np.float32([kp1[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
        dst = np.float32([kp2[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
        H, mask = cv2.findHomography(src, dst, cv2.RANSAC, 5.0)
        if H is None or mask is None:
            candidates.append({"id": doc.id, "status": "homography_failed"})
            continue

        inliers = int(mask.sum())
        inlier_ratio = inliers / max(1, len(good))
        min_inlier_ratio = float(match_cfg.get("min_inlier_ratio", 0.15))
        if inlier_ratio < min_inlier_ratio:
            candidates.append(
                {
                    "id": doc.id,
                    "status": "below_min_inlier_ratio",
                    "inlier_ratio": inlier_ratio,
                }
            )
            continue

        score = inlier_ratio * 1000 + inliers
        candidates.append(
            {
                "id": doc.id,
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
            best_id = doc.id
            best_score = score
            best_warp = (warped_match, warped_ocr, warped_alt)

    diagnostics = {"candidates": candidates}
    if best_id is None or best_warp is None:
        return None, None, None, None, diagnostics

    return best_id, best_warp[0], best_warp[1], best_warp[2], diagnostics
