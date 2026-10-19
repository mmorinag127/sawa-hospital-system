const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const net = require('node:net');
const { spawn, execFileSync } = require('node:child_process');
const { createRequire } = require('node:module');
const { createHash } = require('node:crypto');
const root = path.resolve(__dirname, '../..');
const requireFrontend = createRequire(path.join(root, 'frontend/package.json'));
const { webkit } = requireFrontend('@playwright/test');
const reference = 'aa7f2469101a1161c81553f189b094f5f9f6a914';
const proof = process.argv.find(arg => arg.startsWith('--proof='))?.slice('--proof='.length);
assert.ok(proof && path.isAbsolute(proof) && proof.startsWith(path.join(root,'tmp') + path.sep), 'new explicit own-WT --proof directory required');
fs.mkdirSync(proof);
const development = process.argv.includes('--development');
const original = process.argv.includes('--original');
const app = fs.mkdtempSync(path.join(proof, 'next-'));
const outcomes = [];
let server, browser;
const git = file => execFileSync('git', ['show', reference + ':' + file], { cwd: root });

async function main() {
  for (const dir of ['pages', 'src/components', 'src/hooks', 'src/services', 'tests/helpers']) fs.mkdirSync(path.join(app, dir), {recursive:true});
  fs.symlinkSync(fs.realpathSync(path.join(root, 'frontend/node_modules')), path.join(app, 'node_modules'));
  fs.writeFileSync(path.join(app, 'package.json'), '{"private":true}');
  fs.writeFileSync(path.join(app, 'next.config.js'), 'module.exports={reactStrictMode:true,devIndicators:false,experimental:{cpus:1,scrollRestoration:true}}');
  const boundary = original ? git('frontend/src/components/NavigationBoundary.tsx') : fs.readFileSync(path.join(root,'frontend/src/components/NavigationBoundary.tsx'));
  fs.writeFileSync(path.join(app, 'src/components/NavigationBoundary.tsx'), boundary);
  for (const file of ['hooks/useH1LeaveGuard.ts','hooks/useMenuMasterLeaveGuard.ts','hooks/useH1EditorOwner.ts',
    'services/apiClient.ts','services/browserSession.ts','services/loginDestination.ts']) {
    fs.writeFileSync(path.join(app, 'src', file), git('frontend/src/' + file));
  }
  fs.writeFileSync(path.join(app, 'tests/helpers/navigation-null-next-page.tsx'), git('frontend/tests/helpers/navigation-null-next-page.tsx'));
  fs.writeFileSync(path.join(app, 'pages/test-page.tsx'), `import Editor from '../tests/helpers/navigation-null-next-page';import {useNavigationBoundary} from '../src/components/NavigationBoundary';import Link from 'next/link';
export default function Page(){const boundary=useNavigationBoundary();return <><Editor/><Link id="guarded-link" href="/D" onNavigate={event=>{if(!boundary.allow('/D'))event.preventDefault()}}>leave</Link></>}`);
  for (const route of ['A','B','C','D','unknown']) fs.writeFileSync(path.join(app, 'pages', route + '.tsx'), "export {default} from './test-page';");
  fs.writeFileSync(path.join(app, 'pages/_app.jsx'), `import {useState,useEffect} from 'react';import Router from 'next/router';import {NavigationBoundary} from '../src/components/NavigationBoundary';
export default function App({Component,pageProps}){const[epoch,setEpoch]=useState(0),[mounted,setMounted]=useState(false);useEffect(()=>{window.nativeRouter=Router},[]);return <><button id="remount" onClick={()=>setEpoch(v=>v+1)}>remount</button>{mounted?<NavigationBoundary key={epoch}><Component {...pageProps}/></NavigationBoundary>:<button id="mount" onClick={()=>setMounted(true)}>mount</button>}</>}`);
  const socket = net.createServer(); await new Promise((resolve,reject) => {socket.once('error',reject);socket.listen(0, '127.0.0.1', resolve)});
  const port = socket.address().port; await new Promise(resolve => socket.close(resolve));
  const log = fs.openSync(path.join(app, 'server.log'), 'w');
  if(!development){
    server=spawn(process.execPath,[requireFrontend.resolve('next/dist/bin/next'),'build',app,'--webpack'],{cwd:app,detached:true,stdio:['ignore',log,log],env:{...process.env,NEXT_TELEMETRY_DISABLED:'1'}});
    const exit=await new Promise(resolve=>server.once('exit',resolve));assert.equal(exit,0,'owned Next build failed; see server.log');
  }
  server = spawn(process.execPath, [requireFrontend.resolve('next/dist/bin/next'),development?'dev':'start',app,...(development?['--webpack']:[]),'--hostname','127.0.0.1','--port',String(port)],
    {cwd:app,detached:true,stdio:['ignore',log,log],env:{...process.env,NEXT_TELEMETRY_DISABLED:'1'}}); fs.closeSync(log);
  const origin = 'http://127.0.0.1:' + port;
  for (let i=0;i<120;i++) {
    if (server.exitCode !== null) throw new Error('Next exited; ' + app);
    try { if ((await fetch(origin + '/A')).ok) break; } catch { /* The owned server may not be listening yet. */ }
    if (i===119) throw new Error('owned server timeout');
    await new Promise(resolve => setTimeout(resolve,500));
  }
  for (const route of ['B','C','D','unknown']) assert.equal((await fetch(origin + '/' + route)).status,200);
  browser = await webkit.launch({headless:true});
  const page = await browser.newPage();
  await page.route('**/*', route => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
  await page.addInitScript(() => sessionStorage.setItem('auth_header','Bearer synthetic-local-fixture'));
  let accept=false, confirms=0;
  const errors=[];
  page.on('pageerror', error=>errors.push(error.message));
  page.on('dialog', async dialog => {confirms++;await(accept?dialog.accept():dialog.dismiss())});
  const ready = () => page.waitForFunction(() => window.driver?.ready && window.driver.idle());
  const editor=page.getByRole('textbox',{name:'draft'});
  const alert=page.getByRole('alert').filter({hasText:'編集内容を保持して移動を停止'});
  const snapshot=()=>page.evaluate(()=>window.driver.snapshot());
  const mount=async()=>{await page.locator('#mount').click();await ready()};
  const choose=async mode=>{await page.evaluate(mode=>window.driver.mode(mode),mode);await page.waitForTimeout(50)};
  const settle=async url=>{await page.waitForURL(origin+url);await page.waitForTimeout(150)};
  const fresh=async()=>{accept=true;await page.goto(origin+'/A');await mount();accept=false;await page.evaluate(async()=>{await window.driver.push('/B');await window.driver.push('/C')})};
  const reload=async()=>{accept=true;await page.reload();await mount();accept=false};
  const remount=async()=>{await page.evaluate(()=>{window.previousDriver=window.driver});await page.locator('#remount').click();await page.waitForFunction(()=>window.driver!==window.previousDriver);await ready()};
  const unknownState=(kind,owned)=>kind==='null'?null:kind==='non-next'?{unrelated:true}:kind==='app-router'?{__NA:true}:
    kind==='marked-non-next'?{sawa_navigation:owned}:kind==='marked-app-router'?{__NA:true,sawa_navigation:owned}:
    {__N:true,url:'/test-page',as:'/unknown',options:{shallow:true},...(kind==='foreign'?{sawa_navigation:{token:'11111111-1111-4111-8111-111111111111',index:99}}:{})};
  for(const guard of ['h1','menu']){
    for(const kind of ['null','non-next','app-router','unowned-next','marked-non-next','marked-app-router','foreign']){
      await fresh();await reload();await choose(guard);
      const owned=(await snapshot()).state.sawa_navigation;
      const state=unknownState(kind,owned);
      await page.evaluate(state=>window.driver.seedUnknown(state),state);
      await editor.fill('pre-remount draft');await page.evaluate(()=>history.back());await settle('/unknown');await alert.waitFor();
      const unknown=await snapshot();await remount();const remounted=await snapshot();
      outcomes.push({guard,kind,unknown,remounted});
      assert.deepEqual(remounted.state,state,'remount must never stamp old reload/foreign ownership');
      assert.equal(remounted.length,unknown.length);
      await choose(guard);await editor.fill('new instance retained draft');
      const count=confirms;
      assert.equal(await page.evaluate(()=>window.driver.allow('/D')),false);
      assert.equal(await page.evaluate(()=>window.driver.allowedPush('/D')),false);
      await page.locator('#guarded-link').click();await page.waitForTimeout(80);
      assert.equal(confirms,count,'unknown ownership stops before any confirm');
      assert.equal((await snapshot()).path,'/unknown');assert.deepEqual((await snapshot()).state,state);
      assert.equal(await editor.inputValue(),'new instance retained draft');await alert.waitFor();
    }
    await fresh();await choose(guard);await editor.fill('known C draft');await page.evaluate(()=>window.scrollTo(0,380));
    const c=await snapshot(),start=confirms;
    await page.evaluate(()=>history.back());await settle('/C');
    const cancelled=await snapshot();
    assert.deepEqual(cancelled.state,c.state);assert.equal(cancelled.routerKey,c.routerKey);assert.equal(cancelled.scrollY,c.scrollY);
    assert.equal(confirms,start+1);assert.equal(await editor.inputValue(),'known C draft');
    accept=true;await page.evaluate(()=>history.back());await settle('/B');const b=await snapshot();accept=false;
    await page.evaluate(()=>history.forward());await settle('/B');const forward=await snapshot();
    assert.deepEqual(forward.state,b.state);assert.equal(forward.routerKey,b.routerKey);assert.equal(forward.scrollY,b.scrollY);
    accept=true;await page.evaluate(()=>history.forward());await settle('/C');accept=false;
    assert.equal(confirms,start+4);assert.equal((await snapshot()).length,c.length);
    await page.getByRole('button',{name:'save',exact:true}).click();const saved=await snapshot();
    await reload();assert.deepEqual((await snapshot()).state.sawa_navigation,saved.state.sawa_navigation);
    await remount();await choose(guard);await editor.fill('known reload remount draft');const known=await snapshot();
    await page.evaluate(()=>history.back());await settle('/C');assert.deepEqual((await snapshot()).state,known.state);
    assert.equal((await snapshot()).routerKey,known.routerKey);assert.equal(await editor.inputValue(),'known reload remount draft');
    outcomes.push({guard,knownBothDirections:true,reloadRemount:true,keyAndScrollPreserved:true});
  }
  for(const kind of ['null','non-next','foreign']){
    await fresh();const saved=await snapshot();
    await page.evaluate(state=>window.driver.seedUnknown(state),unknownState(kind,saved.state.sawa_navigation));
    await page.reload();await page.locator('#mount').waitFor();
    if(kind==='foreign') await page.evaluate(()=>new Promise(resolve=>{const done=()=>{window.nativeRouter.events.off('routeChangeComplete',done);resolve()};window.nativeRouter.events.on('routeChangeComplete',done);history.back()}));
    else await page.evaluate(()=>history.back());
    await settle('/unknown');
    const beforeMount=await page.evaluate(()=>({state:history.state,length:history.length}));
    await mount();const afterMount=await snapshot();
    outcomes.push({kind,firstEffectPop:true,beforeMount,afterMount});
    assert.deepEqual(afterMount.state,beforeMount.state);assert.equal(afterMount.state?.sawa_navigation,undefined);
    await choose('h1');await editor.fill('first effect retained draft');const count=confirms;
    assert.equal(await page.evaluate(()=>window.driver.allowedPush('/D')),false);assert.equal(confirms,count);
    assert.equal(await editor.inputValue(),'first effect retained draft');
  }
  await fresh();const reloadOwner=(await snapshot()).state.sawa_navigation;
  await page.evaluate(state=>window.driver.seedUnknown(state),unknownState('foreign',reloadOwner));
  await page.reload();await page.locator('#mount').waitFor();await page.evaluate(()=>history.back());
  await page.waitForURL(origin+'/unknown');await mount();await choose('h1');await editor.fill('immediate native route draft');
  const concurrent=await snapshot(),concurrentConfirm=confirms;
  assert.notEqual(concurrent.state?.sawa_navigation?.token,reloadOwner.token);
  assert.equal(await page.evaluate(()=>window.driver.allowedPush('/D')),false);assert.equal(confirms,concurrentConfirm);
  assert.equal(await editor.inputValue(),'immediate native route draft');outcomes.push({firstEffectImmediateMount:true,concurrent});
  await fresh();const old=(await snapshot()).state.sawa_navigation;
  await page.evaluate(()=>window.driver.seedUnknown({unrelated:true}));await page.evaluate(()=>history.back());await settle('/unknown');
  await remount();await page.evaluate(()=>window.driver.allowedPush('/D'));await settle('/D');
  assert.notEqual((await snapshot()).state.sawa_navigation.token,old.token);assert.equal(await alert.count(),0);
  outcomes.push({cleanUnknownRestart:true});
  if(development){const replay=await page.evaluate(()=>window.effectReplay);assert.ok(replay.setup>=2&&replay.cleanup>=1);outcomes.push({actualStrictModeReplay:replay})}
  assert.deepEqual(errors,[]);
  fs.writeFileSync(path.join(proof,'results.json'),JSON.stringify({outcomes,errors,reference,boundarySha256:createHash('sha256').update(boundary).digest('hex'),next:requireFrontend('next/package.json').version,node:process.version,development,scope:'Actual installed Next/WebKit; synthetic editor, not stg or business input proof'},null,2));
  console.log(JSON.stringify({groups:outcomes.length,app,proof}));
}
main().catch(error=>{console.error(error);process.exitCode=1}).finally(async()=>{
  if(browser) await browser.close();
  if(server && server.exitCode===null){const closed=new Promise(resolve=>server.once('exit',resolve));process.kill(-server.pid,'SIGTERM');await closed}
  if(fs.existsSync(path.join(app,'node_modules')))fs.unlinkSync(path.join(app,'node_modules'));
  fs.writeFileSync(path.join(proof,'observed.json'),JSON.stringify(outcomes,null,2));
  fs.writeFileSync(path.join(app,'cleanup.json'),JSON.stringify({browserClosed:!browser?.isConnected(),serverExited:!server||server.exitCode!==null||server.signalCode!==null,linkRemoved:!fs.existsSync(path.join(app,'node_modules')),reference,
    original},null,2));
  console.log(app);
});

