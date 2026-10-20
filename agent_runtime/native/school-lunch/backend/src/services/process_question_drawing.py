"""Unmodified canonical kitchen SVG and hit regions from the same geometry."""
import base64
import hashlib
import math
from src.services.menu_process_route_service import load_template, options
from src.services.named_svg_route_service import _parse, _equipment_bounds


def drawing(template_id):
    svg, manifest = load_template(template_id)
    root = _parse(svg)
    view_box = [float(n) for n in root.get('viewBox', '').split()]
    if len(view_box) != 4 or not all(math.isfinite(n) for n in view_box) or min(view_box[2:]) <= 0:
        raise ValueError('原本図面の座標範囲を確認できません')
    elements = {e.get('id'): e for e in root.iter() if e.get('id')}
    parents = {child: parent for parent in root.iter() for child in parent}
    template = next(t for t in options()['templates'] if t['template_id'] == template_id)
    equipment = []
    for item in template['equipment']:
        if item['type'] is None:
            continue
        if item['id'] not in elements:
            raise ValueError('原本に設備がありません: ' + item['id'])
        left, top, right, bottom = _equipment_bounds(elements[item['id']], parents)
        x, y, width, height = view_box
        if not (x <= left < right <= x + width and y <= top < bottom <= y + height):
            raise ValueError('設備が原本図面の座標範囲外です: ' + item['id'])
        equipment.append({'id': item['id'], 'name': item['name'], 'bounds': [left, top, right-left, bottom-top]})
    return {'template_id': template_id, 'template_version': manifest['version'],
            'sha256': hashlib.sha256(svg).hexdigest(), 'view_box': view_box,
            'title': root.findtext('{http://www.w3.org/2000/svg}title') or template_id,
            'svg_base64': base64.b64encode(svg).decode('ascii'), 'equipment': equipment}
