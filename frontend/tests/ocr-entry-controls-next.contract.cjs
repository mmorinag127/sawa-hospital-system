const assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path');
const { webkit } = require('@playwright/test');
const output = path.resolve(process.env.OCR_COMPONENT_OUTPUT || '../tmp/browser');
fs.mkdirSync(output,{recursive:true});
const pages=process.argv.slice(2).length?process.argv.slice(2):['pdf-upload','ocr-queue','ocr-results'];
assert.ok(pages.every(name=>['pdf-upload','ocr-queue','ocr-results'].includes(name)));
// Synthetic, read-only component API/identity. No PDF/business job mutation.
(async () => {
  const browser = await webkit.launch({ headless:true });
  try {
    for (const width of [390,1440]) for (const name of pages) {
      const page = await browser.newPage({ viewport:{width,height:1000} });
      const requests=[], errors=[];
      page.on('pageerror',e=>errors.push(e.message));
      await page.addInitScript(()=>{
        sessionStorage.setItem('auth_header','Bearer component-only');
        sessionStorage.setItem('sawa_auth_cache_generation','component');
      });
      let release, produced;
      const held = new Promise(resolve=>{release=resolve;});
      const started = new Promise(resolve=>{produced=resolve;});
      const heldPath = name==='pdf-upload'?'/api/facilities':name==='ocr-queue'?'/api/ingest/jobs':'/api/orders';
      await page.route('**/api/**',async route=>{
        const request=route.request(),p=new URL(request.url()).pathname;
        requests.push({method:request.method(),path:p});
        if(request.method()!=='GET'){errors.push('Forbidden component write '+p);return route.fulfill({status:500,json:{detail:'writes forbidden'}});}
        const responses={
          '/api/auth/me':{role:'operator'},
          '/api/facilities':{facilities:[],revision:'component-r1',source:'canonical_json'},
          '/api/ingest/uploads':{items:[]},
          '/api/ingest/jobs':{items:[{id:'component-only-job',status:'error',attempts:2,generation:'a'.repeat(32),generation_attempts:2,last_error:'synthetic ingest error',pdf_uri:'gs://fixture/input.pdf',message_id:'component-mail',failure_kind:'business',created_at:null,updated_at:null,started_at:null,finished_at:null,received_at:null}]},
          '/api/system/status':{ocr_pipeline:{status:'misconfigured',inflight:0,max_inflight:null}},
          '/api/orders':{orders:[]},
        };
        if(!(p in responses)){errors.push('Unmatched GET '+p);return route.fulfill({status:500,json:{detail:'unexpected boundary'}});}
        if(p===heldPath){produced();await held;}
        return route.fulfill({json:responses[p]});
      });
      await page.goto((process.env.OCR_COMPONENT_ORIGIN || 'http://localhost:3221')+'/hospital/'+name);
      await started;
      if(name==='pdf-upload') {
        await page.waitForFunction(()=>document.querySelectorAll('[aria-disabled="true"],input:disabled').length>0);
      } else {
        const button=page.getByRole('button',{name:name==='ocr-queue'?'滞留をまとめて再試行':'更新中...',exact:true});
        await button.waitFor(); assert.equal(await button.isDisabled(),true);
      }
      release();
      if(name==='pdf-upload') {
        const month=page.locator('input[type="month"]'); await month.fill('2026-10'); assert.equal(await month.inputValue(),'2026-10');
        await page.locator('details.advanced').first().locator('summary').click();
        const memo=page.getByPlaceholder('例: 大和なでしこ'); await memo.fill(' 000 raw ');assert.equal(await memo.inputValue(),' 000 raw ');
        const force=page.locator('input[type="checkbox"]').first();await force.check();assert.equal(await force.isChecked(),true);await force.uncheck();
        const file=page.locator('input[type="file"]');
        assert.equal(await file.getAttribute('accept'),'application/pdf');assert.equal(await file.getAttribute('multiple'),'');
        await file.setInputFiles([{name:'selection-one.bin',mimeType:'application/octet-stream',buffer:Buffer.from('selection only')},{name:'selection-two.bin',mimeType:'application/octet-stream',buffer:Buffer.from('selection only')}]);
        assert.equal(await file.evaluate(n=>n.files instanceof FileList&&n.files.length),2);
        await page.getByRole('button',{name:'2件のPDFを登録',exact:true}).waitFor();
        // No facility/week selected, no submit, no claim these bytes are valid PDFs.
      } else if(name==='ocr-queue') {
        await page.getByText('component-only-job',{exact:true}).waitFor();
        assert.equal(await page.getByPlaceholder('テンプレートID').count(),0);
        await page.getByText('エラー: synthetic ingest error',{exact:true}).waitFor();
        const select=page.locator('select');await select.selectOption('error');assert.equal(await select.inputValue(),'error');
      } else {
        await page.getByRole('button',{name:'更新',exact:true}).waitFor();
        const checkbox=page.locator('input[type="checkbox"]');await checkbox.check();assert.equal(await checkbox.isChecked(),true);await checkbox.uncheck();assert.equal(await checkbox.isChecked(),false);
      }
      assert.equal(requests.some(r=>r.method!=='GET'),false);
      const clipping=await page.locator('input,select,button,textarea').evaluateAll(nodes=>nodes.filter(n=>{const r=n.getBoundingClientRect();return r.width>0&&n.getAttribute('aria-hidden')!=='true'&&(r.left < -1||r.right > innerWidth+1);}).map(n=>n.outerHTML));
      fs.writeFileSync(path.join(output,name+'-'+width+'.json'),JSON.stringify({requests,errors,clipping},null,2));
      await page.screenshot({path:path.join(output,name+'-'+width+'.png'),fullPage:true});
      assert.deepEqual(errors,[]);assert.deepEqual(clipping,[]);
      console.log(JSON.stringify({name,width,heldReadDisabled:true,writes:0,controls:true}));
      await page.close();
    }
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
