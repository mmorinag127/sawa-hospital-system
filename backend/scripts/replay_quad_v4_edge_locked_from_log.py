# v4: constrain refit to pixels close to the initial minAreaRect edge; never jump to interior percentile lines.
from pathlib import Path
import json, cv2, fitz, numpy as np
from PIL import Image, ImageDraw
BASE=Path('/Users/mmorinag/Sawa/2025.12/workspace/tmp/outer_quad_eval_correct_20260426')
MAN=json.loads((BASE/'stg_week_2026-04-26_2026-04-30_manifest.local.json').read_text())
OUT=BASE/'quad_v4_edge_locked'; OUT.mkdir(exist_ok=True)

def render(pdf,dpi=220):
 doc=fitz.open(pdf); p=doc.load_page(0); pix=p.get_pixmap(matrix=fitz.Matrix(dpi/72,dpi/72),alpha=False); return Image.frombytes('RGB',[pix.width,pix.height],pix.samples)
def binarize(arr):
 gray=cv2.cvtColor(arr,cv2.COLOR_RGB2GRAY); gray=cv2.GaussianBlur(gray,(3,3),0); return cv2.threshold(gray,0,255,cv2.THRESH_BINARY_INV+cv2.THRESH_OTSU)[1]
def masks(thr):
 H,W=thr.shape; hm=cv2.morphologyEx(thr,cv2.MORPH_OPEN,cv2.getStructuringElement(cv2.MORPH_RECT,(max(28,W//90),1)),iterations=1); vm=cv2.morphologyEx(thr,cv2.MORPH_OPEN,cv2.getStructuringElement(cv2.MORPH_RECT,(1,max(28,H//90))),iterations=1); comb=cv2.bitwise_or(hm,vm); comb=cv2.morphologyEx(comb,cv2.MORPH_CLOSE,cv2.getStructuringElement(cv2.MORPH_RECT,(9,9)),iterations=1); comb=cv2.dilate(comb,cv2.getStructuringElement(cv2.MORPH_RECT,(3,3)),iterations=1); return hm,vm,comb
def choose_component(comb):
 H,W=comb.shape; n,lab,stats,_=cv2.connectedComponentsWithStats((comb>0).astype('uint8'),8); best=None
 for i in range(1,n):
  x,y,w,h,area=stats[i]
  if w<W*.35 or h<H*.25 or area<800: continue
  score=w*h+area*8
  if y<H*.03: score*=.75
  row={'label':i,'x':int(x),'y':int(y),'w':int(w),'h':int(h),'area':int(area),'score':float(score)}
  if best is None or row['score']>best['score']: best=row
 source='large_grid_component'
 if best is None:
  for i in range(1,n):
   x,y,w,h,area=stats[i]; row={'label':i,'x':int(x),'y':int(y),'w':int(w),'h':int(h),'area':int(area),'score':float(area)}
   if best is None or row['score']>best['score']: best=row
  source='fallback_largest_component'
 return (lab==best['label']).astype('uint8')*255,best,source
def order_quad(pts):
 pts=np.asarray(pts,float); s=pts.sum(axis=1); d=np.diff(pts,axis=1).reshape(-1); return [pts[np.argmin(s)],pts[np.argmin(d)],pts[np.argmax(s)],pts[np.argmax(d)]]
def line2(p1,p2):
 p1=np.asarray(p1,float); p2=np.asarray(p2,float); v=p2-p1; n=np.linalg.norm(v); return np.array([v[0]/n,v[1]/n,p1[0],p1[1]],float)
def dist(points,line):
 vx,vy,x0,y0=line; n=np.array([-vy,vx],float); return (points-np.array([x0,y0],float))@n
def fit(points,fallback):
 if len(points)>=30:
  vx,vy,x0,y0=cv2.fitLine(np.asarray(points,np.float32),cv2.DIST_L1,0,0.01,0.01).flatten(); return np.array([vx,vy,x0,y0],float),'fit'
 return fallback,'fallback'
def intersect(a,b):
 vx1,vy1,x1,y1=a; vx2,vy2,x2,y2=b; A=np.array([[vx1,-vx2],[vy1,-vy2]],float)
 if abs(np.linalg.det(A))<1e-8: return None
 t,_=np.linalg.solve(A,np.array([x2-x1,y2-y1],float)); return np.array([x1+vx1*t,y1+vy1*t],float)
def line_points(mask): return np.column_stack(np.where(mask>0)[::-1]).astype(float)
def refine(comp_mask,hm,vm):
 ys,xs=np.where(comp_mask>0); pts=np.column_stack([xs,ys]).astype(np.float32); init=order_quad(cv2.boxPoints(cv2.minAreaRect(pts))); tl,tr,br,bl=init
 base={'top':line2(tl,tr),'right':line2(tr,br),'bottom':line2(bl,br),'left':line2(tl,bl)}
 hp=line_points(cv2.bitwise_and(comp_mask,hm)); vp=line_points(cv2.bitwise_and(comp_mask,vm)); cp=line_points(comp_mask)
 refined={}; src={}; counts={}
 for e,b in base.items():
  primary=hp if e in ('top','bottom') and len(hp)>0 else vp if e in ('left','right') and len(vp)>0 else cp
  # HARD LOCK: only pixels close to the initial outer edge, so it cannot jump to an internal row.
  d=np.abs(dist(primary,b)); band=22.0
  sel=primary[d<=band]
  if len(sel)<30:
   d2=np.abs(dist(cp,b)); sel=cp[d2<=band]
  refined[e],src[e]=fit(sel,b); counts[e]=int(len(sel))
 q=[intersect(refined['top'],refined['left']),intersect(refined['top'],refined['right']),intersect(refined['bottom'],refined['right']),intersect(refined['bottom'],refined['left'])]
 if any(x is None for x in q): q=init
 return init,q,base,refined,src,counts
def collect(mask,line,p1,p2,search=14,samples=360):
 H,W=mask.shape; p1=np.asarray(p1,float); p2=np.asarray(p2,float); vx,vy,x0,y0=line; n=np.array([-vy,vx],float); n=n/(np.linalg.norm(n) or 1); hits=0; offs=[]; miss=maxmiss=0
 for i in range(samples):
  p=p1*(1-i/(samples-1))+p2*(i/(samples-1)); found=None
  for dd in range(-search,search+1):
   q=p+n*dd; x=int(round(q[0])); y=int(round(q[1]))
   if 0<=x<W and 0<=y<H and mask[y,x]>0: found=dd; break
  if found is None: miss+=1; maxmiss=max(maxmiss,miss)
  else: hits+=1; offs.append(abs(found)); miss=0
 L=float(np.linalg.norm(p2-p1)); return {'hit_rate':round(hits/samples,4),'mean_abs_offset_px':round(float(np.mean(offs)) if offs else 999,3),'max_abs_offset_px':round(float(np.max(offs)) if offs else 999,3),'gap_max_px_est':round(maxmiss*L/max(1,samples-1),1)}
def validate(hm,vm,comb,q,lines):
 tl,tr,br,bl=q; return {'top':collect(cv2.bitwise_or(hm,comb),lines['top'],tl,tr),'right':collect(cv2.bitwise_or(vm,comb),lines['right'],tr,br),'bottom':collect(cv2.bitwise_or(hm,comb),lines['bottom'],bl,br),'left':collect(cv2.bitwise_or(vm,comb),lines['left'],tl,bl)}
def reasons(m,src,comp_source):
 out=[]
 if comp_source!='large_grid_component': out.append(comp_source)
 for e,x in m.items():
  if src.get(e)!='fit': out.append(f'{e}_line_not_refit')
  if x['hit_rate']<0.78: out.append(f'{e}_hit_rate_low:{x["hit_rate"]}')
  if x['mean_abs_offset_px']>4.5: out.append(f'{e}_offset_high:{x["mean_abs_offset_px"]}')
  if x['gap_max_px_est']>140: out.append(f'{e}_gap_large:{x["gap_max_px_est"]}')
 return out
def draw(img,init,ref,metrics,comp,out,title,status,rs):
 ov=img.copy(); d=ImageDraw.Draw(ov)
 def poly(q,color,w):
  pts=[tuple(map(float,p)) for p in q]; d.line([pts[0],pts[1],pts[2],pts[3],pts[0]],fill=color,width=w)
  for lab,p in zip(['TL','TR','BR','BL'],pts): d.ellipse([p[0]-9,p[1]-9,p[0]+9,p[1]+9],fill=color); d.text((p[0]+11,p[1]+6),lab,fill=color)
 d.rectangle([comp['x'],comp['y'],comp['x']+comp['w'],comp['y']+comp['h']],outline=(0,80,255),width=3); poly(init,(255,0,0),4); poly(ref,(0,180,0),5)
 lines=[title,f'status={status}','blue=component bbox / red=minAreaRect / green=edge-locked refit']
 if rs: lines.append(', '.join(rs)[:190])
 for e in ['top','right','bottom','left']:
  x=metrics[e]; lines.append(f'{e}: hit={x["hit_rate"]} mean={x["mean_abs_offset_px"]} gap={x["gap_max_px_est"]}')
 y=20
 for s in lines:
  d.rectangle([16,y-4,1450,y+24],fill=(255,255,255)); d.text((20,y),s,fill=(0,0,0)); y+=27
 ov.save(out)
results=[]
for r in MAN:
 pdf=Path(r['local_pdf']); case=OUT/f"{r['facility_code']}_{r['id']}"; case.mkdir(exist_ok=True)
 try:
  img=render(pdf); arr=np.array(img); thr=binarize(arr); hm,vm,comb=masks(thr); cm,comp,comp_source=choose_component(comb); init,ref,base,lines,src,counts=refine(cm,hm,vm); metrics=validate(hm,vm,comb,ref,lines); rs=reasons(metrics,src,comp_source); status='ok' if not rs else 'ng'; overlay=case/'quad_overlay.png'; draw(img,init,ref,metrics,comp,overlay,f"{r['facility_code']} {r['id']} {pdf.name}",status,rs); shift={k:round(float(np.linalg.norm(np.asarray(ref[i])-np.asarray(init[i]))),2) for i,k in enumerate(['TL','TR','BR','BL'])}
  results.append({**r,'status':status,'reasons':rs,'component_source':comp_source,'component':comp,'edge_sources':src,'edge_point_counts':counts,'initial_quad_px':[[round(float(x),2),round(float(y),2)] for x,y in init],'refined_quad_px':[[round(float(x),2),round(float(y),2)] for x,y in ref],'corner_shift_px':shift,'metrics':metrics,'overlay':str(overlay)})
 except Exception as e: results.append({**r,'status':'error','reasons':['pipeline_error'],'error':repr(e)})
(OUT/'quad_results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
pages=[]
for i,r in enumerate(results,1):
 im=Image.open(r['overlay']).convert('RGB') if r.get('overlay') else Image.new('RGB',(2200,3000),'white'); cw,ch=2480,3508; can=Image.new('RGB',(cw,ch),'white'); d=ImageDraw.Draw(can); d.text((50,35),f"{i}/{len(results)} {r['facility_code']} {r['id']} {r['status']} {Path(r['local_pdf']).name}",fill=(0,0,0));
 if r.get('reasons'): d.text((50,70),', '.join(r['reasons'])[:220],fill=(180,0,0))
 im.thumbnail((cw-100,ch-130),Image.Resampling.LANCZOS); can.paste(im,((cw-im.width)//2,115)); pages.append(can)
pdf_out=OUT/'stg_week_2026-04-26_2026-04-30_quad_v4_single.pdf'; pages[0].save(pdf_out,save_all=True,append_images=pages[1:],resolution=200)
md=OUT/'quad_report.md'; lines=['# stg 2026-04-26~2026-04-30 quad v4 edge-locked report','',f'- canonical_source: Cloud SQL stg orders.week_code exact match + archived_at is null + upload message_id',f'- count: {len(results)}',f'- single_pdf: `{pdf_out}`','', '|#|facility|order|status|reasons|top|right|bottom|left|overlay|','|---:|---|---|---|---|---:|---:|---:|---:|---|']
for i,r in enumerate(results,1):
 m=r.get('metrics') or {}; g=lambda e:m.get(e,{}).get('hit_rate',''); lines.append(f"|{i}|{r['facility_code']}|{r['id']}|{r['status']}|{', '.join(r.get('reasons') or [])}|{g('top')}|{g('right')}|{g('bottom')}|{g('left')}|`{r.get('overlay','')}`|")
md.write_text('\n'.join(lines),encoding='utf-8')
print(json.dumps({'count':len(results),'ok':sum(r['status']=='ok' for r in results),'ng':sum(r['status']=='ng' for r in results),'error':sum(r['status']=='error' for r in results),'single_pdf':str(pdf_out),'report':str(md),'json':str(OUT/'quad_results.json')},ensure_ascii=False,indent=2))
