import cv2
import numpy as np


def build_images_for_match_and_ocr(png_bytes: bytes):
    n = np.frombuffer(png_bytes, dtype=np.uint8)
    img = cv2.imdecode(n, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("failed to decode PNG bytes")

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    denoised = cv2.fastNlMeansDenoising(
        gray, None, h=10, templateWindowSize=7, searchWindowSize=21
    )
    _, binary = cv2.threshold(denoised, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    match = cv2.cvtColor(binary, cv2.COLOR_GRAY2BGR)

    ocr = _remove_lines_for_ocr(binary)
    ocr = cv2.cvtColor(ocr, cv2.COLOR_GRAY2BGR)
    return match, ocr


def _remove_lines_for_ocr(binary_image):
    img = binary_image.copy()
    h_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (40, 1))
    h_lines = cv2.morphologyEx(img, cv2.MORPH_OPEN, h_kernel, iterations=1)

    v_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, 40))
    v_lines = cv2.morphologyEx(img, cv2.MORPH_OPEN, v_kernel, iterations=1)

    lines = cv2.bitwise_or(h_lines, v_lines)
    cleaned = img.copy()
    cleaned[lines > 0] = 255
    return cleaned
