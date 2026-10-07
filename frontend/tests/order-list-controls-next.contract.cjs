const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {webkit} = require('@playwright/test');
const output = path.resolve('../tmp/browser');
// Read-only synthetic identity/list boundary, not OCR or writer-flow acceptance.
(async () => {
  fs.mkdirSync(output,{recursive:true});
  const browser = await webkit.launch({headless:true});
  try {
    for(const width of [390,1440]) {
      const page=await browser.newPage({viewport:{width,height:1000}});
      const requests=[], errors=[];
      page.on('pageerror',e=>errors.push(e.message));
      await page.addInitScript(()=>{
        sessionStorage.setItem('auth_header','Bearer component-only');
        sessionStorage.setItem('sawa_auth_cache_generation','component');
      });
      await page.route('**/api/**',async route=>{
        const req=route.request(), url=new URL(req.url());
        requests.push({method:req.method(),url:url.pathname+url.search});
        if(req.method()!=='GET') {errors.push('Forbidden write '+url.pathname);return route.fulfill({status:500,json:{detail:'writes forbidden'}});}
        const data={
          '/api/auth/me':{role:'operator'},
          '/api/facilities':{facilities:[],revision:'component-r1',source:'canonical_json'},
          '/api/orders':{orders:[{id:'component-only',status:'未着',document:'component',facility:null,week:null}],has_more:false,next_cursor:null},
          '/api/orders/component-only/ocr-output':{facility_candidates:[]},
        };
        if(!(url.pathname in data)) {errors.push('Unmatched GET '+url.pathname);return route.fulfill({status:500,json:{detail:'unexpected boundary'}});}
        return route.fulfill({json:data[url.pathname]});
      });
      await page.goto('http://localhost:40590/hospital/orders');
      await page.getByRole('button',{name:'開く',exact:true}).first().waitFor();
      const select=page.locator('select');
      assert.deepEqual(await select.locator('option').evaluateAll(ns=>ns.map(n=>n.value)),['','アップロード済み','未着','要確認','確定','エラー']);
      await select.selectOption('未着'); assert.equal(await select.inputValue(),'未着');
      const input=page.getByPlaceholder('施設名 / 週 / 注文ID / 受付ID');
      await input.fill(' 000 raw '); assert.equal(await input.inputValue(),' 000 raw ');
      await input.fill('');
      const boxes=page.locator('input[type="checkbox"]'); assert.equal(await boxes.count(),2);
      for(let i=0;i<2;i++) {await boxes.nth(i).check();assert.equal(await boxes.nth(i).isChecked(),true);await boxes.nth(i).uncheck();}
      const toggle=page.getByRole('button',{name:'開く',exact:true}).first();
      await toggle.click(); assert.equal(await page.getByRole('button',{name:'閉じる',exact:true}).first().getAttribute('aria-expanded'),'true');
      await page.getByRole('button',{name:'閉じる',exact:true}).first().click();
      await page.getByRole('button',{name:'最新に更新',exact:true}).click();
      const clipping=await page.locator('input,select,button').evaluateAll(ns=>ns.filter(n=>{const r=n.getBoundingClientRect();return r.width>0&&(r.left < -1||r.right>innerWidth+1);}).map(n=>n.outerHTML));
      assert.equal(requests.some(r=>r.method!=='GET'),false);
      assert.deepEqual(errors,[]); assert.deepEqual(clipping,[]);
      fs.writeFileSync(path.join(output,width+'.json'),JSON.stringify({requests,errors,clipping},null,2));
      await page.screenshot({path:path.join(output,width+'.png'),fullPage:true});
      console.log(JSON.stringify({width,writes:0,filters:true,toggle:true}));
      await page.close();
    }
  } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
