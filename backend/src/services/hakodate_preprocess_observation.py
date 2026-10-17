"""Structural input observations; removed ink is not a glyph classifier."""
import hashlib
import json
import math
import re

CONTRACT = 'hakodate_preprocess_observation/v1'


def geometry(region):
    return {key:region.get(key) for key in ('sheet_cell','bbox','polygon','role','field')}


def geometry_hash(region):
    return hashlib.sha256(json.dumps(geometry(region),sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def pixels(image):
    return {'shape':list(image.shape),'sha256':hashlib.sha256(image.tobytes()).hexdigest()}


def observe(source, region, actual_warp, crop_box, source_hash):
    import cv2
    import numpy as np
    polygon = region.get('polygon')
    if polygon is not None:
        points = np.asarray(polygon,dtype=np.float32)
        height,width = source.shape[:2]
        if (points.shape != (4,2) or not np.isfinite(points).all()
                or (points[:,0] < 0).any() or (points[:,0] > width).any()
                or (points[:,1] < 0).any() or (points[:,1] > height).any()
                or not cv2.isContourConvex(points) or cv2.contourArea(points,oriented=True) <= 0):
            raise ValueError('hakodate_observation_geometry_invalid')
        w = round(max(np.linalg.norm(points[1]-points[0]),np.linalg.norm(points[2]-points[3])))
        h = round(max(np.linalg.norm(points[3]-points[0]),np.linalg.norm(points[2]-points[1])))
        if w < 2 or h < 2:
            raise ValueError('hakodate_observation_geometry_invalid')
        expected_box = [math.floor(min(p[0] for p in polygon)),math.floor(min(p[1] for p in polygon)),
                        math.ceil(max(p[0] for p in polygon)),math.ceil(max(p[1] for p in polygon))]
        transform = cv2.getPerspectiveTransform(points,np.float32([[0,0],[w-1,0],[w-1,h-1],[0,h-1]]))
        expected = cv2.warpPerspective(source,transform,(w,h),flags=cv2.INTER_CUBIC,
            borderMode=cv2.BORDER_CONSTANT,borderValue=(255,255,255))
    else:
        box = region.get('bbox')
        height,width = source.shape[:2]
        if (not isinstance(box,list) or len(box) != 4
                or any(type(v) not in (int,float) or not math.isfinite(v) for v in box)
                or not 0 <= box[0] < box[2] <= width
                or not 0 <= box[1] < box[3] <= height):
            raise ValueError('hakodate_observation_geometry_invalid')
        # Independently replay the prescribed corner_cc padding, not the
        # caller's crop coordinates: two white crops can still be different cells.
        expected_box = [max(0,math.floor(box[0]-2)),max(0,math.floor(box[1]-10)),
                        min(width,math.ceil(box[2]+2)),min(height,math.ceil(box[3]+10))]
        x0,y0,x1,y1 = expected_box
        expected = source[y0:y1,x0:x1]
    if list(crop_box) != expected_box:
        raise ValueError('hakodate_observation_source_crop_changed')
    x0,y0,x1,y1 = crop_box
    return {'contract':CONTRACT,'source_sha256':source_hash,'geometry_sha256':geometry_hash(region),
        'source_crop_box':expected_box,'actual_warp':pixels(actual_warp),'expected_warp':pixels(expected),
        'bbox_ink_pixels':int(np.count_nonzero(np.any(source[y0:y1,x0:x1] < 255,axis=2)))}


def validate_metadata(source):
    observation = source.get('recognizer_preprocess_observation')
    if not isinstance(observation,dict) or observation.get('contract') != CONTRACT:
        raise ValueError('hakodate_observation_invalid')
    for key in ('source_sha256','geometry_sha256'):
        if not re.fullmatch(r'[0-9a-f]{64}',str(observation.get(key) or '')):
            raise ValueError('hakodate_observation_invalid')
    if observation['geometry_sha256'] != geometry_hash(source):
        raise ValueError('hakodate_observation_geometry_changed')
    for key in ('actual_warp','expected_warp'):
        stage = observation.get(key)
        if (not isinstance(stage,dict) or not re.fullmatch(r'[0-9a-f]{64}',str(stage.get('sha256') or ''))
                or not isinstance(stage.get('shape'),list) or len(stage['shape']) != 3
                or stage['shape'][2] != 3 or any(type(v) is not int or v <= 0 for v in stage['shape'])):
            raise ValueError('hakodate_observation_invalid')
    box = observation.get('source_crop_box')
    if (not isinstance(box,list) or len(box) != 4 or any(type(v) is not int for v in box)
            or not 0 <= box[0] < box[2] or not 0 <= box[1] < box[3]
            or box != source.get('ocr_cell_crop_bbox_px')
            or type(observation.get('bbox_ink_pixels')) is not int
            or not 0 <= observation['bbox_ink_pixels'] <= (box[2]-box[0])*(box[3]-box[1])):
        raise ValueError('hakodate_observation_invalid')
    contact = source.get('recognizer_input_evidence')
    if not isinstance(contact,dict) or observation.get('contact') != contact:
        raise ValueError('hakodate_observation_contact_changed')
    if source.get('recognizer_preprocess_ink_removed') is not bool(contact.get('all_white') and observation['bbox_ink_pixels']):
        raise ValueError('hakodate_observation_flag_changed')
    # A wrong source transform remains a blocker, even for a nonwhite crop.
    return 'hakodate_preprocess_ink_loss' if observation['actual_warp'] != observation['expected_warp'] else None


def validate_artifact(data, records, contact_bytes, source_receipt):
    from io import BytesIO
    from PIL import Image
    from src.services.hakodate_ocr_checkpoint_service import load_recognizer_checkpoint
    from src.services.workflow_attempt_guard_service import canonical_snapshot_json
    from src.hakodate_best_method_runtime.run_text_recognizer_corner_noise_trial import build_recognizer_contact_sheet
    manifest = json.loads(data)
    if not isinstance(manifest,dict) or manifest.get('contract') != CONTRACT:
        raise ValueError('hakodate_observation_artifact_invalid')
    checkpoint = manifest['checkpoint']
    if (checkpoint['source']['selected_pdf_receipt'] != source_receipt['selected_pdf_receipt']
            or hashlib.sha256(canonical_snapshot_json(checkpoint['resume_item']['configured_input']).encode()).hexdigest() != source_receipt['configured_input_digest']
            or checkpoint['expected_sheet_cells'] != source_receipt['expected_sheet_cells']):
        raise ValueError('hakodate_observation_source_changed')
    state = load_recognizer_checkpoint(checkpoint=checkpoint,item={**checkpoint['resume_item'],
        'selected_pdf_receipt':checkpoint['source']['selected_pdf_receipt']})
    image, regions, _, skipped = build_recognizer_contact_sheet(rectified_fax_bgr=state['raw_rectified'],
        regions=state['regions'],line_mask=None,mode='corner_cc')
    with Image.open(BytesIO(contact_bytes)) as saved:
        if image.size != saved.size or image.tobytes() != saved.convert('RGB').tobytes():
            raise ValueError('hakodate_observation_contact_changed')
    if skipped or len(records) != len(regions):
        raise ValueError('hakodate_observation_cells_changed')
    observations = []
    for record, region in zip(records,regions):
        # Derive contact evidence directly from the verified sheet, not raw OCR.
        from src.services.hakodate_recognizer_input_evidence import contact_crop_evidence
        x0,y0,x1,y1 = region['ocr_contact_crop_box']
        if record.get('recognizer_input_evidence') != contact_crop_evidence(image,[[x0,y0],[x1,y0],[x1,y1],[x0,y1]]):
            raise ValueError('hakodate_observation_contact_changed')
        if (validate_metadata(record) or geometry(record) != geometry(region)
                or record['recognizer_preprocess_observation'] != region['recognizer_preprocess_observation']):
            raise ValueError('hakodate_observation_source_or_warp_changed')
        observations.append(record['recognizer_preprocess_observation'])
    if manifest.get('observations') != observations:
        raise ValueError('hakodate_observation_artifact_changed')
