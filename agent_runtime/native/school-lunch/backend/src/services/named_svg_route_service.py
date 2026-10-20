"""Strict, local named-point SVG renderer; does not infer business routes."""
import hashlib
import json
import math
import re
import unicodedata
import xml.etree.ElementTree as ET

NS = 'http://www.w3.org/2000/svg'
ET.register_namespace('', NS)


def _tag(name):
    return '{%s}%s' % (NS, name)


def hide_standard_routes(root):
    """Source example strokes are not part of the generated work graph."""
    for el in root.iter():
        if el.get('id') == 'standard-route':
            el.set('display', 'none')
            el.set('style', 'display:none !important')


def _number(value):
    if isinstance(value, bool):
        raise ValueError('Invalid coordinate')
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise ValueError('Invalid coordinate') from None
    if not math.isfinite(result):
        raise ValueError('Non-finite coordinate')
    return result


def _parse(svg_bytes):
    if re.search(br'<!\s*(?:DOCTYPE|ENTITY)', svg_bytes, re.I):
        raise ValueError('DTD/entities forbidden')
    try:
        root = ET.fromstring(svg_bytes)
    except ET.ParseError as exc:
        raise ValueError('Invalid SVG') from exc
    if root.tag != _tag('svg'):
        raise ValueError('Expected SVG root')
    for el in root.iter():
        local = el.tag.split('}')[-1].lower()
        if local in {'script', 'foreignobject', 'animate', 'animatetransform', 'set'}:
            raise ValueError('Active SVG forbidden')
        for key, value in el.attrib.items():
            key = key.split('}')[-1].lower()
            if key.startswith('on') or key == 'base':
                raise ValueError('Event handler forbidden')
            if key == 'href' and not value.startswith('#'):
                raise ValueError('External references forbidden')
            _safe_css(value)
        if local == 'style':
            _safe_css(el.text or '')
    return root


def _safe_css(value):
    # Escapes/comments can disguise CSS imports and resource URLs: reject them.
    if '\\' in value or '/*' in value or re.search(r'@import|expression\s*\(', value, re.I):
        raise ValueError('Unsafe SVG style')
    for url in re.findall(r'url\s*\((.*?)\)', value, re.I):
        if not url.strip().strip('\"\'').startswith('#'):
            raise ValueError('External CSS reference forbidden')


def _equipment_bounds(element, parents):
    shapes = [e for e in element.iter() if e.tag in {_tag('rect'), _tag('circle'), _tag('ellipse')}]
    if not shapes or element.tag not in {_tag('g'), _tag('rect'), _tag('circle'), _tag('ellipse')}:
        raise ValueError('Equipment has no supported geometry')
    boxes = []
    for shape in shapes:
        ancestor = shape
        while ancestor is not None:
            if ancestor.get('transform') or re.search(r'(?:^|;)\s*transform\s*:', ancestor.get('style', ''), re.I):
                raise ValueError('Transformed equipment forbidden')
            ancestor = parents.get(ancestor)
        if shape.tag == _tag('rect'):
            x, y, w, h = (_number(shape.get(k)) for k in ('x', 'y', 'width', 'height'))
        else:
            cx, cy = _number(shape.get('cx')), _number(shape.get('cy'))
            rx = _number(shape.get('r' if shape.tag == _tag('circle') else 'rx'))
            ry = rx if shape.tag == _tag('circle') else _number(shape.get('ry'))
            x, y, w, h = cx - rx, cy - ry, rx * 2, ry * 2
        if w <= 0 or h <= 0:
            raise ValueError('Invalid equipment bounds')
        boxes.append((x, y, x + w, y + h))
    return min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)


def validate_pair(svg_bytes, manifest):
    """Validate exact SVG/manifest version pairing and all anchor geometry."""
    if type(manifest.get('schema_version')) is not int or type(manifest.get('version')) is not int or manifest.get('schema_version') != 1 or manifest.get('version') < 1:
        raise ValueError('Unsupported manifest version')
    if not isinstance(manifest.get('template_id'), str) or not manifest['template_id']:
        raise ValueError('Missing template ID')
    filename = manifest.get('svg_file', '')
    if not filename or '/' in filename or '\\' in filename or filename in {'.', '..'}:
        raise ValueError('svg_file must be basename')
    if hashlib.sha256(svg_bytes).hexdigest() != manifest.get('svg_sha256'):
        raise ValueError('SVG hash mismatch')
    root = _parse(svg_bytes)
    vb = [_number(v) for v in manifest.get('view_box', [])]
    if vb != [0, 0, 1900, 1340]:
        raise ValueError('Unexpected view box')
    if [_number(v) for v in root.get('viewBox', '').replace(',', ' ').split()] != vb:
        raise ValueError('SVG view box mismatch')
    elements = {}
    parents = {child: parent for parent in root.iter() for child in parent}
    for el in root.iter():
        eid = el.get('id')
        if eid:
            if eid in elements:
                raise ValueError('Duplicate SVG ID')
            elements[eid] = el
    ids = set()
    anchor_ids = set()
    primary_names = set()
    for collection in ('points', 'fields'):
        if not isinstance(manifest.get(collection), list):
            raise ValueError('Missing manifest collection')
        for item in manifest[collection]:
            if not item.get('id') or item['id'] in ids:
                raise ValueError('Duplicate/missing manifest ID')
            ids.add(item['id'])
            eid = item.get('svg_element_id')
            if eid in anchor_ids or eid not in elements:
                raise ValueError('Missing/duplicate SVG anchor')
            anchor_ids.add(eid)
            el = elements[eid]
            ancestor = el
            hidden = False
            while ancestor is not None:
                if ancestor.get('transform'):
                    raise ValueError('Transformed anchors forbidden')
                if ancestor.tag == _tag('g') and (ancestor.get('display') == 'none' or re.search(r'(?:^|;)\s*display\s*:\s*none(?:\s*!important)?\s*(?:;|$)', ancestor.get('style', ''))):
                    hidden = True
                ancestor = parents.get(ancestor)
            if not hidden:
                raise ValueError('Anchor must be in hidden group')
            if not item.get('name'):
                raise ValueError('Missing name')
            x, y = _number(item.get('x')), _number(item.get('y'))
            if not 0 <= x <= vb[2] or not 0 <= y <= vb[3]:
                raise ValueError('Out of bounds')
            if collection == 'points':
                if item['name'] in primary_names:
                    raise ValueError('Duplicate point name')
                primary_names.add(item['name'])
                if el.tag != _tag('circle') or (_number(el.get('cx')), _number(el.get('cy'))) != (x, y):
                    raise ValueError('Point anchor mismatch')
                if not isinstance(item.get('aliases'), list) or any(not isinstance(a, str) or not a for a in item['aliases']):
                    raise ValueError('Invalid aliases')
                if not item.get('kind'):
                    raise ValueError('Missing point kind')
                if item['kind'] in {'equipment_port', 'dispatch'}:
                    equipment_id = item.get('equipment_id')
                    if equipment_id not in elements:
                        raise ValueError('Missing equipment')
                    equipment = elements[equipment_id]
                    if item['kind'] == 'dispatch' and equipment.tag != _tag('rect'):
                        raise ValueError('Dispatch requires rect')
                    left, top, right, bottom = _equipment_bounds(equipment, parents)
                    fraction = _number(item.get('port_fraction', 0.5))
                    if not 0 <= fraction <= 1:
                        raise ValueError('Invalid port fraction')
                    sides = {'西接続': (left, top + (bottom - top) * fraction), '北接続': (left + (right - left) * fraction, top), '東接続': (right, top + (bottom - top) * fraction), '南接続': (left + (right - left) * fraction, bottom)}
                    side = '北接続' if item['kind'] == 'dispatch' else item.get('side')
                    if side not in sides or sides[side] != (x, y):
                        raise ValueError('Equipment port geometry mismatch')
            else:
                w, h = _number(item.get('width')), _number(item.get('height'))
                if w <= 0 or h <= 0 or x + w > vb[2] or y + h > vb[3]:
                    raise ValueError('Invalid field bounds')
                if el.tag != _tag('rect') or tuple(_number(el.get(k)) for k in ('x', 'y', 'width', 'height')) != (x, y, w, h):
                    raise ValueError('Field anchor mismatch')
                size, lines = _number(item.get('font_size')), item.get('max_lines')
                if size <= 0 or isinstance(lines, bool) or not isinstance(lines, int) or lines < 1 or size * 1.2 * lines > h:
                    raise ValueError('Invalid field typography')
    point_ids = {p['id'] for p in manifest['points']}
    for point in manifest['points']:
        if 'label_anchor' in point and (not isinstance(point['label_anchor'], str) or point['label_anchor'] not in point_ids):
            raise ValueError('Unknown label anchor')
    for el in root.iter():
        if el.get('data-field-id') and el.get('data-field-id') not in {f['id'] for f in manifest['fields']}:
            raise ValueError('Unregistered dynamic field')
    return root


def resolve_line_styles(manifest):
    styles=manifest.get('line_styles')
    if not isinstance(styles,dict) or set(styles)!={'solid','dashed'}:
        raise ValueError('マスターに solid/dashed の線種設定を明示してください')
    for spec in styles.values():
        if not isinstance(spec,dict) or set(spec)!={'dasharray'} or not isinstance(spec['dasharray'],list) or any(type(v) not in (int,float) or not math.isfinite(v) or v<=0 for v in spec['dasharray']):
            raise ValueError('マスターの線種設定が不正です')
    if styles['solid']['dasharray'] or len(styles['dashed']['dasharray'])<2 or len(styles['dashed']['dasharray'])%2:
        raise ValueError('マスターの solid/dashed 線種設定が不正です')
    return styles


def render(svg_bytes, manifest, request):
    line_styles=resolve_line_styles(manifest)
    if not isinstance(request, dict):
        raise ValueError('Request must be an object')
    root = validate_pair(svg_bytes, manifest)
    if request.get('template_id') != manifest['template_id'] or type(request.get('template_version')) is not int or request.get('template_version') != manifest['version']:
        raise ValueError('Template version mismatch')
    for el in root:
        if el.tag == _tag('title'):
            el.text = '厨房作業動線図 — 名前指定による生成'
        elif el.tag == _tag('desc'):
            el.text = '名前付き接続点と明示された区間線種から生成。業務経路の正しさを自動判定したものではありません。'
    points = {}
    for point in manifest['points']:
        for name in set([point['name'], *point['aliases']]):
            points.setdefault(name, []).append(point)
    fields = {f['id']: f for f in manifest['fields']}
    values = request.get('fields')
    routes = request.get('routes')
    if not isinstance(values, dict) or not isinstance(routes, list):
        raise ValueError('Invalid routes/fields')
    resolved_values = {}
    for key, value in values.items():
        matches = [f for f in fields.values() if key in (f['id'], f['name'])]
        if len(matches) != 1 or matches[0]['id'] in resolved_values:
            raise ValueError('Unknown/ambiguous/duplicate field: %s' % key)
        resolved_values[matches[0]['id']] = value
    values = resolved_values
    rich = request.get('field_runs', {})
    if not isinstance(rich, dict):
        raise ValueError('Invalid colored fields')
    rich_values = {}
    for key, lines in rich.items():
        matches = [f for f in fields.values() if key in (f['id'], f['name'])]
        if len(matches) != 1 or matches[0]['id'] in values or matches[0]['id'] in rich_values:
            raise ValueError('Unknown/duplicate colored field')
        field = matches[0]
        if not isinstance(lines, list) or not lines or len(lines) > field['max_lines']:
            raise ValueError('Invalid colored field lines')
        for line in lines:
            if not isinstance(line, list) or not line:
                raise ValueError('Invalid colored field runs')
            for run in line:
                if not isinstance(run, dict) or not isinstance(run.get('text'), str) or not isinstance(run.get('color'), str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', run['color']):
                    raise ValueError('Invalid colored field text/style')
            if _width(''.join(run['text'] for run in line), field['font_size']) > field['width']:
                raise ValueError('Colored field overflow')
        rich_values[field['id']] = lines
        values[field['id']] = ''
    placement = request.get('legend_placement', 'bottom')
    if placement not in ('bottom', 'food_names'):
        raise ValueError('Invalid legend placement')
    if placement == 'food_names':
        if 'food_names' not in fields or 'food_names' in values:
            raise ValueError('Derived food legend requires exclusive food_names field')
        values['food_names'] = ''
    for parent in root.iter():
        for child in list(parent):
            if child.get('data-field-id') in values:
                parent.remove(child)
    hide_standard_routes(root)
    used_ids = {el.get('id') for el in root.iter()}
    if any(i and i.startswith('named-route-') for i in used_ids):
        raise ValueError('Reserved generated ID in source')
    defs = ET.SubElement(root, _tag('defs'))
    layer = ET.Element(_tag('g'), {'id': 'named-route-layer'})
    annotations = ET.Element(_tag('g'), {'id': 'named-route-annotations'})
    material_annotations = {}
    shared_edges = {}
    route_ids = set()
    spec = fields.get('food_names') if placement == 'food_names' else manifest.get('route_legend')
    if not isinstance(spec, dict):
        raise ValueError('Missing route legend bounds')
    lx, ly, lw, lh, fs = (_number(spec.get(k)) for k in ('x', 'y', 'width', 'height', 'font_size'))
    limit = spec.get('max_lines')
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1 or fs <= 0 or lw <= 0 or lh <= 0 or lx < 0 or ly < 0 or lx + lw > 1900 or ly + lh > 1340 or limit * fs * 1.2 > lh:
        raise ValueError('Invalid/overflowing route legend')
    legend = ET.Element(_tag('g'), {'id': 'named-route-legend'})
    route_map = {}
    for route in routes:
        if not isinstance(route, dict) or not isinstance(route.get('id'), str) or not route['id'] or route['id'] in route_map:
            raise ValueError('Invalid route ID/object')
        route_map[route['id']] = route
    for rid, route in route_map.items():
        members = route.get('member_route_ids', [])
        if not isinstance(members, list) or any(not isinstance(m, str) or m not in route_map or m == rid for m in members) or len(set(members)) != len(members) or ('member_route_ids' in route and len(members) < 2):
            raise ValueError('Invalid shared route members')
        if type(route.get('show_in_legend', True)) is not bool:
            raise ValueError('Invalid legend visibility')
        if route.get('show_in_legend') is False and not members:
            raise ValueError('Only explicit shared routes can omit legend')
    def visit(rid, active, done):
        if rid in active:
            raise ValueError('Cyclic shared route members')
        if rid not in done:
            for member in route_map[rid].get('member_route_ids', []):
                visit(member, active | {rid}, done)
            done.add(rid)
    done = set()
    for rid in route_map:
        visit(rid, set(), done)
    legend_row = 0
    previous_group = None
    def legend_text(label, color):
        nonlocal legend_row
        if not isinstance(label, str) or '\n' in label or _width(label, fs) > lw or legend_row >= limit:
            raise ValueError('Invalid/overflowing legend label')
        text = ET.SubElement(legend, _tag('text'), {'x': str(lx), 'y': str(ly + fs + legend_row * fs * 1.2), 'font-size': str(fs), 'fill': color, 'style': 'font-size:%spx;fill:%s' % (fs, color)})
        text.text = label
        legend_row += 1
    points_by_id = {p['id']: p for p in manifest['points']}
    for index, route in enumerate(routes):
        if not isinstance(route, dict):
            raise ValueError('Route must be an object')
        rid = route.get('id')
        if not isinstance(rid, str) or not rid or rid in route_ids:
            raise ValueError('Invalid route ID')
        route_ids.add(rid)
        color, style, names = route.get('color'), route.get('style'), route.get('points')
        if not isinstance(color, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', color) or style not in {'solid', 'dashed'}:
            raise ValueError('Invalid route style')
        if not isinstance(names, list) or len(names) < 2:
            raise ValueError('Route needs at least two named points')
        resolved = []
        for name in names:
            if not isinstance(name, str) or len(points.get(name, [])) != 1:
                raise ValueError('Unknown or ambiguous point: %s' % name)
            resolved.append(points[name][0])
        segment_styles = route.get('segment_styles', [style] * (len(resolved) - 1))
        if not isinstance(segment_styles, list) or len(segment_styles) != len(resolved) - 1 or any(s not in ('solid', 'dashed') for s in segment_styles):
            raise ValueError('Invalid segment styles')
        def selection(key, defaults):
            if key not in route:
                return {p['id'] for p in defaults}
            if not isinstance(route[key], list):
                raise ValueError('Invalid %s' % key)
            selected = set()
            for name in route[key]:
                if not isinstance(name, str) or len(points.get(name, [])) != 1:
                    raise ValueError('Unknown or ambiguous %s point' % key)
                pid = points[name][0]['id']
                if pid in selected or sum(p['id'] == pid for p in resolved) != 1:
                    raise ValueError('Duplicate or non-unique route %s point' % key)
                selected.add(pid)
            return selected
        arrows = selection('arrow_points', [p for p in resolved[1:] if p['kind'] != 'waypoint'] + [resolved[-1]])
        material = route.get('material_number')
        if 'material_number' in route and (type(material) is not int or not 1 <= material <= 20):
            raise ValueError('Material number must be an integer 1..20')
        materials = route.get('material_numbers', [material] if material is not None else [])
        if 'material_numbers' in route and ('material_number' in route or not isinstance(materials, list) or not materials or any(type(m) is not int or not 1 <= m <= 20 for m in materials) or len(set(materials)) != len(materials)):
            raise ValueError('Invalid material numbers')
        number_text = ''.join(chr(0x2460 + m - 1) for m in materials)
        number_format = route.get('number_format', 'list')
        if number_format not in ('list', 'range') or (number_format == 'range' and (len(materials) < 2 or materials != list(range(materials[0], materials[-1] + 1)))):
            raise ValueError('Invalid material number format')
        if number_format == 'range':
            number_text = chr(0x2460 + materials[0] - 1) + '〜' + chr(0x2460 + materials[-1] - 1)
        if 'label_points' in route and not materials:
            raise ValueError('Label points require material number')
        labels = selection('label_points', [p for p in resolved if p['kind'] != 'waypoint']) if materials else set()
        transfers = route.get('equipment_transfers', [])
        if not isinstance(transfers, list):
            raise ValueError('Invalid equipment transfers')
        transfer_edges = set()
        for pair in transfers:
            if not isinstance(pair, list) or len(pair) != 2 or any(not isinstance(n, str) or len(points.get(n, [])) != 1 for n in pair):
                raise ValueError('Invalid equipment transfer points')
            a, b = [points[n][0] for n in pair]
            edge_indices = [i for i, (start, end) in enumerate(zip(resolved, resolved[1:])) if (start['id'], end['id']) == (a['id'], b['id'])]
            if a['id'] == b['id'] or a['kind'] != 'equipment_port' or b['kind'] != 'equipment_port' or not a.get('equipment_id') or a.get('equipment_id') != b.get('equipment_id') or len(edge_indices) != 1 or edge_indices[0] in transfer_edges:
                raise ValueError('Equipment transfer must be unique adjacent ports of the same equipment')
            transfer_edges.add(edge_indices[0])
        marker_id = 'named-route-arrow-%d' % index
        marker = ET.SubElement(defs, _tag('marker'), {'id': marker_id, 'viewBox': '0 0 8 8', 'refX': '7', 'refY': '4', 'markerWidth': '8', 'markerHeight': '8', 'markerUnits': 'userSpaceOnUse', 'orient': 'auto', 'overflow': 'visible'})
        ET.SubElement(marker, _tag('path'), {'d': 'M 1 1 L 7 4 L 1 7', 'fill': 'none', 'stroke': color, 'stroke-width': '1.15', 'stroke-linecap': 'round', 'stroke-linejoin': 'round'})
        distance = 0
        previous_style = None
        for edge_index, (a, b) in enumerate(zip(resolved, resolved[1:])):
            if (a['x'], a['y']) == (b['x'], b['y']):
                raise ValueError('Zero-length segment')
            if edge_index in transfer_edges:
                ET.SubElement(layer, _tag('g'), {'data-route-id': rid, 'data-equipment-transfer': a['equipment_id'], 'data-from-point': a['id'], 'data-to-point': b['id']})
                distance = 0
                previous_style = None
                continue
            edge_style = segment_styles[edge_index]
            if edge_style != previous_style:
                distance = 0
            phase = round(distance % sum(line_styles['dashed']['dasharray']), 6) if edge_style == 'dashed' else 0
            attrs = {'x1': str(a['x']), 'y1': str(a['y']), 'x2': str(b['x']), 'y2': str(b['y']), 'stroke': color, 'stroke-width': '1.35', 'stroke-linecap': 'round', 'stroke-linejoin': 'round', 'data-route-id': rid, 'data-route-ids': json.dumps([rid], ensure_ascii=False), 'data-segment-style': edge_style}
            if route.get('member_route_ids'):
                attrs['data-member-route-ids'] = json.dumps(route['member_route_ids'], ensure_ascii=False)
            arrow = b['id'] in arrows
            route_segment = {'route_id': rid, 'segment_index': edge_index, 'arrow_at_end': arrow, 'from_point': a['id'], 'to_point': b['id']}
            if route.get('member_route_ids'):
                route_segment['member_route_ids'] = route['member_route_ids']
            attrs['data-route-segments'] = json.dumps([route_segment], ensure_ascii=False)
            if arrow:
                attrs['marker-end'] = 'url(#%s)' % marker_id
            if edge_style == 'dashed':
                attrs['stroke-dasharray'] = ' '.join(str(v) for v in line_styles['dashed']['dasharray'])
                # SVG positive offset consumes the already travelled pattern;
                # negative would restart at the complementary phase.
                attrs['stroke-dashoffset'] = str(phase)
            # Do not coalesce different directions, colours, heat styles, or dash
            # phases: doing so changes what an individual material route means.
            key = (a['x'], a['y'], b['x'], b['y'], color.lower(), edge_style, phase)
            if key in shared_edges:
                edge = shared_edges[key]
                ids = json.loads(edge.get('data-route-ids'))
                if rid not in ids:
                    ids.append(rid)
                edge.set('data-route-ids', json.dumps(ids, ensure_ascii=False))
                edge.set('data-route-segments', json.dumps(json.loads(edge.get('data-route-segments')) + [route_segment], ensure_ascii=False))
                if route.get('member_route_ids'):
                    members = json.loads(edge.get('data-member-route-ids', '[]'))
                    edge.set('data-member-route-ids', json.dumps(list(dict.fromkeys(members + route['member_route_ids'])), ensure_ascii=False))
                if arrow:
                    edge.set('marker-end', 'url(#%s)' % marker_id)
            else:
                shared_edges[key] = ET.SubElement(layer, _tag('line'), attrs)
            distance += math.hypot(b['x'] - a['x'], b['y'] - a['y'])
            previous_style = edge_style
        seen_labels = set()
        for point in resolved:
            if point['id'] in labels and point['id'] not in seen_labels:
                seen_labels.add(point['id'])
                anchor = points_by_id[point['label_anchor']] if 'label_anchor' in point else None
                position = (anchor['x'], anchor['y']) if anchor else (point['x'] + 7, point['y'] - 7)
                by_identity = material_annotations.setdefault(position, {})
                identity = (color.lower(), tuple(materials), number_text)
                entry = by_identity.setdefault(identity, {'route_ids': [], 'point_ids': []})
                if rid not in entry['route_ids']:
                    entry['route_ids'].append(rid)
                if point['id'] not in entry['point_ids']:
                    entry['point_ids'].append(point['id'])
        label = route.get('label')
        if not isinstance(label, str):
            raise ValueError('Invalid legend label')
        _width(label, fs)
        group = route.get('legend_group')
        if group is not None and (not isinstance(group, str) or not group or '\n' in group):
            raise ValueError('Invalid legend group')
        if route.get('show_in_legend', True):
            if placement == 'food_names' and group is not None and group != previous_group:
                group_color = route.get('legend_group_color', '#171717')
                if not isinstance(group_color, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', group_color):
                    raise ValueError('Invalid group color')
                legend_text(group, group_color)
            previous_group = group
            legend_lines = route.get('legend_lines', [label])
            if not isinstance(legend_lines, list) or not legend_lines or any(not isinstance(s, str) or not s for s in legend_lines) or ''.join(legend_lines) != label:
                raise ValueError('Legend lines must preserve the complete label')
            for line_index, line in enumerate(legend_lines):
                legend_text((number_text + ' ' if materials and line_index == 0 else '') + line, color)
    for (x, y), by_identity in material_annotations.items():
        for stack_index, ((color, materials, number_text), entry) in enumerate(by_identity.items()):
            attrs = {'x': str(x), 'y': str(y - stack_index * 17), 'style': 'font-size:15px;fill:%s;stroke:none' % color, 'data-route-id': entry['route_ids'][0], 'data-route-ids': json.dumps(entry['route_ids'], ensure_ascii=False), 'data-point-id': entry['point_ids'][0], 'data-point-ids': json.dumps(entry['point_ids'], ensure_ascii=False), 'data-material-numbers': json.dumps(materials)}
            if len(materials) == 1:
                attrs['data-material-number'] = str(materials[0])
            text = ET.SubElement(annotations, _tag('text'), attrs)
            text.text = number_text
    for fid, value in values.items():
        field = fields[fid]
        if fid in rich_values:
            size = field['font_size']
            text = ET.SubElement(root, _tag('text'), {'style': 'font-size:%spx' % size, 'data-rendered-field-id': fid, '{http://www.w3.org/XML/1998/namespace}space': 'preserve'})
            for index, line in enumerate(rich_values[fid]):
                row = ET.SubElement(text, _tag('tspan'), {'x': str(field['x']), 'y': str(field['y'] + size + index * size * 1.2)})
                for run in line:
                    ET.SubElement(row, _tag('tspan'), {'style': 'fill:%s;font-size:%spx' % (run['color'], size)}).text = run['text']
            continue
        if not isinstance(value, str):
            raise ValueError('Field text required')
        lines = value.split('\n')
        size = field['font_size']
        if len(lines) > field['max_lines'] or any(_width(line, size) > field['width'] for line in lines):
            raise ValueError('Field text overflow: %s' % fid)
        text = ET.SubElement(root, _tag('text'), {'x': str(field['x']), 'y': str(field['y'] + size), 'font-size': str(size), 'style': 'font-size:%spx;fill:#171717' % size, 'data-rendered-field-id': fid})
        for index, line in enumerate(lines):
            ET.SubElement(text, _tag('tspan'), {'x': str(field['x']), 'y': str(field['y'] + size + index * size * 1.2)}).text = line
    # All opaque base geometry and daily text precede transport paths. Material
    # annotations remain above every route, including later overlapping routes.
    root.extend([layer, annotations, legend])
    return ET.tostring(root, encoding='utf-8', xml_declaration=True)


def _width(text, size):
    # Conservative em bound rather than font-specific guessed fitting/truncation.
    if any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF or ord(c) in {0xFFFE, 0xFFFF} for c in text):
        raise ValueError('Invalid XML/control text')
    return sum(0 if unicodedata.combining(c) else 1 for c in text) * size
