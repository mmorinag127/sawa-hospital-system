import { expect, test } from '@playwright/test';

test.beforeEach(async ({ page }) => {
  await page.clock.setFixedTime(new Date('2026-11-10T03:00:00Z'));
  await page.addInitScript(() => {
    localStorage.setItem('auth_header','Bearer shipping-fixture');
    sessionStorage.setItem('auth_header','Bearer shipping-fixture');
  });
  await page.route('**/api/**', async route => {
    const url=new URL(route.request().url());
    if(url.pathname.endsWith('/auth/me')) {
      await route.fulfill({json:{role:'admin',systems:['hospital'],email:'fixture@example.test'}}); return;
    }
    if(url.pathname.endsWith('/shipping/status/latest')) {
      const view=url.searchParams.get('view');
      const empty=view==='recent' && !url.searchParams.get('base_date')?.startsWith('2026-07');
      const items=empty?[]:[{tracking_key:'fixture',tracking_number:'1111-1111-1111',facility_name:'表示確認施設',
        status:'輸送中',delivered:false,source:'manual_track',ship_date:'2026-07-21',looked_up_at:'2026-08-03T09:15:34Z',events:[]}];
      await route.fulfill({json:{view,items,summary:{total:items.length,pending:items.length,delivered:0,errors:0,attention:0,facility_missing:0},quota:null}}); return;
    }
    await route.fulfill({json:{items:[],summary:{total:0}}});
  });
});

for(const viewport of [{width:1280,height:720},{width:390,height:844}]) {
  test(`shipping controls are not displaced by menu ${viewport.width}`,async({page},testInfo)=>{
    await page.setViewportSize(viewport);
    await page.goto('/hospital/shipping');
    const parse=page.getByRole('button',{name:'解析してExcelをダウンロード'});
    await expect(parse).toBeEnabled();
    const box=await parse.boundingBox();
    expect(box!.y).toBeGreaterThan(0);
    expect(box!.y+box!.height).toBeLessThan(viewport.height);
    const menu=page.locator('details.compact-navigation');
    await expect(menu).not.toHaveAttribute('open','');
    await menu.locator('summary').click();
    await expect(menu.getByRole('link',{name:'注文一覧',exact:true})).toBeVisible();
    await menu.locator('summary').click();
    await page.getByRole('button',{name:'到着日時を更新してExcelをダウンロード'}).scrollIntoViewIfNeeded();
    await expect(page.getByRole('button',{name:'到着日時を更新してExcelをダウンロード'})).toBeEnabled();
    await page.getByRole('button',{name:'追跡状況を取得'}).scrollIntoViewIfNeeded();
    await expect(page.getByRole('button',{name:'追跡状況を取得'})).toBeEnabled();
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();
    await page.evaluate(()=>scrollTo(0,0));
    await page.screenshot({path:testInfo.outputPath('shipping.png')});
  });
}

test('active default and all results reveal their month; month browsing never changes all into recent',async({page})=>{
  const calls:string[]=[];
  page.on('request',request=>{if(request.url().includes('/shipping/status/latest')) calls.push(request.url());});
  await page.goto('/hospital/shipping-history');
  await expect(page.getByRole('heading',{name:'2026年7月',exact:true})).toBeVisible();
  expect(new URL(calls[0]).searchParams.get('view')).toBe('active');
  await expect(page.getByRole('button',{name:'有効中',exact:true})).toHaveClass(/active/);
  await page.getByRole('button',{name:'全件',exact:true}).click();
  await expect.poll(()=>new URL(calls.at(-1)!).searchParams.get('view')).toBe('all');
  await expect(page.getByRole('heading',{name:'2026年7月',exact:true})).toBeVisible();
  const count=calls.length;
  await page.getByRole('button',{name:'翌月',exact:true}).click();
  await expect(page.getByRole('heading',{name:'2026年8月',exact:true})).toBeVisible();
  await expect(page.getByRole('button',{name:'全件',exact:true})).toHaveClass(/active/);
  expect(calls).toHaveLength(count);
  await page.getByRole('button',{name:'状態を更新',exact:true}).click();
  await expect(page.getByRole('heading',{name:'2026年7月',exact:true})).toBeVisible();
  await page.getByRole('button',{name:'翌月',exact:true}).click();
  await expect(page.getByRole('heading',{name:'2026年8月',exact:true})).toBeVisible();
  await page.getByRole('button',{name:'前月',exact:true}).click();
  await expect(page.getByTestId('shipping-date-group').filter({hasText:'1件'})).toBeVisible();
  await page.getByRole('button',{name:'直近 ±日数',exact:true}).click();
  await expect.poll(()=>new URL(calls.at(-1)!).searchParams.get('view')).toBe('recent');
  await expect(page.getByRole('heading',{name:'2026年11月',exact:true})).toBeVisible();
});
