"""Carry the operator-selected SVG into the legacy editable route exporter."""
import base64
from copy import deepcopy
import hashlib
import xml.etree.ElementTree as ET
from .store import Conflict
from .sandbox import run_blocking


def raster(source):
    from resvg_py import svg_to_bytes
    root = ET.fromstring(source)
    # Source example routes and empty form labels are not today's work. The
    # exporter writes the actual school/date and food legend outside the map.
    for element in root:
        if element.get('id') in ('standard-route', 'sheet-labels'):
            element.set('style', 'display:none !important')
    return svg_to_bytes(svg_string=ET.tostring(root, encoding='unicode'), background='white')


def pixels(image):
    import fitz
    try:
        pix = fitz.Pixmap(image)
        if pix.alpha:
            pix = fitz.Pixmap(pix, 0)
        return pix.width, pix.height, hashlib.sha256(pix.samples).hexdigest()
    except Exception as exc:
        raise Conflict('出力用厨房図面の画像を照合できません') from exc


async def prepare(tools, run):
    async with tools.lock(run['id']):
        current = await run_blocking(tools.store.get, run['id'])
        if current['generation'] != run['generation'] or current['status'] != 'running':
            raise Conflict('依頼は停止されました')
        state = await tools.flow.questions.checked(current)
        if any(not item['saved'] for item in state['documents']):
            raise Conflict('当日の工程を先に保存してください')
        templates = {(item['document'].get('route_config') or {}).get('template_id') for item in state['documents']}
        if len(templates) != 1 or None in templates:
            raise Conflict('全献立で同じ正規厨房図面を選択してください')
        template = next(iter(templates))
        pictures = [await tools.flow.questions.drawing(current, item['menu_id'], template) for item in state['documents']]
        picture = pictures[0]
        if any(candidate != picture for candidate in pictures[1:]):
            raise Conflict('当日の献立で正規図面の画像・版・設備座標が一致しません')
        source = tools.sandbox.path(run['id'])/'question-drawings'/(picture['sha256']+'.svg')
        image = await run_blocking(lambda: raster(source.read_bytes()))
        x, y, width, height = picture['view_box']
        locations = {e['name']: [(e['bounds'][0]+e['bounds'][2]/2-x)/width,
                                 (e['bounds'][1]+e['bounds'][3]/2-y)/height] for e in picture['equipment']}
        if len(locations) != len(picture['equipment']):
            raise Conflict('正規図面の設備名が重複しており座標を一意に渡せません')
        context = current['task_inputs']; facility = context['inputs']['facility_id']
        schools={item['data']['facility']['name'] for item in context['targets']}
        if len(schools)!=1:raise Conflict('選択した学校名が一意ではありません')
        name=next(iter(schools))+' / '+context['inputs']['service_date']+' / '+picture['title']
        if len(name)>200:raise Conflict('出力用図面の学校名・対象日・図面名が保存可能な長さを超えています')
        receipt = context.get('canonical_route_layout')
        image_pixels = await run_blocking(pixels, image)
        binding = {'template_id':template,'template_version':picture['template_version'],'svg_sha256':picture['sha256'],
                   'locations':locations,'pixels':image_pixels}
        async def mutate(stage, method, path, body, files=None):
            latest=await run_blocking(tools.store.get, run['id'])
            if latest['generation']!=run['generation'] or latest['status']!='running':raise Conflict('依頼は停止されました')
            command='canonical-layout:'+picture['sha256']+':'+stage
            request={'name':'canonical_layout_'+stage,'arguments':{'binding':binding,'body':body}}
            previous=await run_blocking(tools.store.command, run['id'],command,request)
            if previous is not None:return previous
            response=await tools.sandbox.request(latest,'school-lunch',method,path,params={'facility_id':facility},body=body,files=files)
            if response.status_code!=200:
                raise Conflict('正規厨房図面の受渡し結果が未確定です。再送せず履歴を確認してください')
            result=response.json();await run_blocking(tools.store.finish_command, run['id'],command,result)
            await run_blocking(tools.store.event, run['id'],'canonical_layout_receipt',{'command_id':command,'layout_id':result['id'],'version':result['version'],'binding':binding})
            return result
        if receipt:
            if receipt['binding'] != {**binding,'pixels':list(binding['pixels'])}:
                raise Conflict('回答した正規図面と出力用図面の出典が一致しません')
            layouts=await tools.flow.read(current,'lunch_get_layouts',{'query':{'facility_id':facility}})
            saved=next((value for value in layouts if value['id']==receipt['id']),None)
        else:
            created=await mutate('create','POST','/api/school-lunch/route-diagrams/layouts',
                {'facility_id':str(facility),'name':name},
                {'file':('canonical.png',image,'image/png')})
            saved=await mutate('locations','PUT','/api/school-lunch/route-diagrams/layouts/'+str(created['id']),
                {'version':created['version'],'locations':locations})
        if (not saved or saved['facility_id']!=facility or saved['name']!=name or saved['locations']!=locations
                or await run_blocking(pixels, base64.b64decode(saved['image'].split(',',1)[1]))!=image_pixels):
            raise Conflict('保存した図面の学校・設備座標・画像が正規原本と一致しません')
        latest=await run_blocking(tools.store.get, run['id'])
        if latest['generation']!=run['generation'] or latest['status']!='running':raise Conflict('依頼は停止されました')
        context=deepcopy(latest['task_inputs'])
        context['canonical_route_layout']={'id':saved['id'],'version':saved['version'],'binding':{**binding,'pixels':list(binding['pixels'])}}
        context['inputs'].update(layout_id=saved['id'],layout_version=saved['version'])
        context['process_questions']['sources']['layout']={key:saved[key] for key in ('id','version','locations')}
        await run_blocking(tools.store.update, run['id'],task_inputs=context)
        return await run_blocking(tools.store.get, run['id'])
