"""Exact contact-crop evidence without loading the recognition runtime."""

import hashlib


def contact_crop_evidence(contact_sheet, polygon):
    if (not isinstance(polygon, list) or len(polygon) != 4
            or any(not isinstance(point, list) or len(point) != 2
                   or any(type(value) is not int for value in point) for point in polygon)):
        raise ValueError("recognizer_contact_crop_invalid")
    x0, y0 = polygon[0]
    x1, y1 = polygon[2]
    if (polygon != [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]
            or not 0 <= x0 < x1 <= contact_sheet.width or not 0 <= y0 < y1 <= contact_sheet.height):
        raise ValueError("recognizer_contact_crop_invalid")
    crop = contact_sheet.crop((x0, y0, x1, y1)).convert("RGB")
    return {"contract": "hakodate_contact_crop/v1", "bbox": [x0, y0, x1, y1],
            "width": x1 - x0, "height": y1 - y0,
            "rgb_sha256": hashlib.sha256(crop.tobytes()).hexdigest(),
            "all_white": crop.getextrema() == ((255, 255), (255, 255), (255, 255))}
