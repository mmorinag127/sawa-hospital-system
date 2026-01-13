import cv2
import numpy as np

from src.services.storage_service import load_bytes_from_uri


def _load_template_image(uri: str) -> np.ndarray:
    if not uri:
        raise ValueError("template image uri is required")
    data = load_bytes_from_uri(uri)
    n = np.frombuffer(data, dtype=np.uint8)
    image = cv2.imdecode(n, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"failed to decode template image: {uri}")
    return image


def choose_template_and_warp(
    templates: dict,
    match_bgr: np.ndarray,
    ocr_bgr: np.ndarray,
) -> tuple[str, np.ndarray, np.ndarray, dict]:
    if not templates:
        raise ValueError("no templates registered")

    best_id = None
    best_score = -1.0
    best_warp = None
    best_template = None

    for template_id, template in templates.items():
        if not isinstance(template, dict):
            continue
        image_uri = template.get("template_image_gcs_uri") or template.get("template_image_uri")
        if not image_uri:
            continue
        tpl_img = _load_template_image(image_uri)
        match_cfg = template.get("match", {}) if isinstance(template.get("match"), dict) else {}
        orb_nfeatures = int(match_cfg.get("orb_nfeatures", 2000))
        min_matches = int(match_cfg.get("min_matches", 25))
        min_inlier_ratio = float(match_cfg.get("min_inlier_ratio", 0.15))

        orb = cv2.ORB_create(nfeatures=orb_nfeatures)
        bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=False)

        kp1, des1 = orb.detectAndCompute(cv2.cvtColor(match_bgr, cv2.COLOR_BGR2GRAY), None)
        kp2, des2 = orb.detectAndCompute(cv2.cvtColor(tpl_img, cv2.COLOR_BGR2GRAY), None)
        if des1 is None or des2 is None:
            continue

        matches = bf.knnMatch(des1, des2, k=2)
        good = []
        for match, neighbor in matches:
            if match.distance < 0.75 * neighbor.distance:
                good.append(match)
        if len(good) < min_matches:
            continue

        src = np.float32([kp1[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
        dst = np.float32([kp2[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
        H, mask = cv2.findHomography(src, dst, cv2.RANSAC, 5.0)
        if H is None or mask is None:
            continue
        inliers = int(mask.sum())
        inlier_ratio = inliers / max(1, len(good))
        if inlier_ratio < min_inlier_ratio:
            continue

        score = inlier_ratio * 1000 + inliers
        if score > best_score:
            warp_cfg = template.get("warp", {}) if isinstance(template.get("warp"), dict) else {}
            output_size = warp_cfg.get("output_size")
            if output_size and len(output_size) == 2:
                width, height = int(output_size[0]), int(output_size[1])
            else:
                height, width = tpl_img.shape[:2]
            warped_match = cv2.warpPerspective(match_bgr, H, (width, height))
            warped_ocr = cv2.warpPerspective(ocr_bgr, H, (width, height))
            best_id = template_id
            best_score = score
            best_warp = (warped_match, warped_ocr)
            best_template = template

    if not best_id or not best_warp or best_template is None:
        raise ValueError("template classification failed")

    return best_id, best_warp[0], best_warp[1], best_template
