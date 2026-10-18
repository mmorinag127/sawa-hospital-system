const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const net = require('node:net');
const { spawn, execFileSync } = require('node:child_process');
const { webkit } = require('@playwright/test');
const frontend = path.resolve(__dirname, '..'), root = path.resolve(frontend, '..');
const proof = path.join(root, 'tmp/navigation-null-proof');
const before = process.argv.includes('--before');
const probe = process.argv.includes('--clean-probe');
const development = process.argv.includes('--development');
const mode = process.argv.find(arg => arg.startsWith('--mode='))?.split('=')[1] || (before ? 'original' : 'current');
const reference = process.argv.find(arg => arg.startsWith('--reference='))?.slice('--reference='.length);
const comparisonBoundary = process.argv.find(arg => arg.startsWith('--boundary-source='))?.slice('--boundary-source='.length);
const expectRegression = process.argv.includes('--expect-clean-regression');
assert.ok(['original', 'current', 'bare', 'comparison'].includes(mode));
if (mode === 'original') assert.match(reference || '', /^[0-9a-f]{40}$/, 'explicit immutable --reference SHA required');
if (mode === 'comparison') assert.ok(comparisonBoundary && path.isAbsolute(comparisonBoundary), 'explicit absolute --boundary-source required');
assert.ok(!expectRegression || (mode === 'comparison' && probe));
fs.mkdirSync(proof, { recursive: true });
const app = fs.mkdtempSync(path.join(proof, `${mode}-next-`));
const outcomes = [], observations = [];
let server, browser, buildExit;

async function main() {
  for (const dir of ['pages', 'src/components', 'src/hooks', 'src/services', 'tests/helpers']) fs.mkdirSync(path.join(app, dir), { recursive: true });
  fs.symlinkSync(fs.realpathSync(path.join(frontend, 'node_modules')), path.join(app, 'node_modules'));
  fs.writeFileSync(path.join(app, 'package.json'), '{"private":true}');
  fs.writeFileSync(path.join(app, 'next.config.js'), 'module.exports={reactStrictMode:true,devIndicators:false,experimental:{cpus:1,scrollRestoration:true}};');
  const boundaryPath = 'frontend/src/components/NavigationBoundary.tsx';
  const boundary = mode === 'original' ? execFileSync('git', ['show', `${reference}:${boundaryPath}`], { cwd: root })
    : mode === 'comparison' ? fs.readFileSync(comparisonBoundary)
      : fs.readFileSync(path.join(root, boundaryPath));
  fs.writeFileSync(path.join(app, 'src/components/NavigationBoundary.tsx'), boundary);
  for (const file of ['hooks/useH1LeaveGuard.ts', 'hooks/useMenuMasterLeaveGuard.ts', 'hooks/useH1EditorOwner.ts',
    'services/apiClient.ts', 'services/browserSession.ts', 'services/loginDestination.ts']) {
    fs.copyFileSync(path.join(frontend, 'src', file), path.join(app, 'src', file));
  }
  fs.copyFileSync(path.join(__dirname, 'helpers/navigation-null-next-page.tsx'), path.join(app, 'tests/helpers/navigation-null-next-page.tsx'));
  fs.writeFileSync(path.join(app, 'pages/test-page.tsx'), "export {default} from '../tests/helpers/navigation-null-next-page';");
  for (const route of ['A', 'B', 'C', 'D', 'E', 'prime', 'unknown', 'recovered', 'later', 'blocked', 'allowed']) {
    fs.writeFileSync(path.join(app, 'pages', `${route}.tsx`), "export {default} from './test-page';");
  }
  fs.writeFileSync(path.join(app, 'pages/_app.jsx'), `import {useState,useEffect} from 'react'; import {NavigationBoundary} from '../src/components/NavigationBoundary';
export default function App({Component,pageProps}){const [epoch,setEpoch]=useState(0);useEffect(()=>{window.remountBoundary=()=>setEpoch(v=>v+1)},[]);
return ${mode === 'bare' ? '<Component {...pageProps}/>' : '<NavigationBoundary key={epoch}><Component {...pageProps}/></NavigationBoundary>'};}`);
  const socket = net.createServer(); await new Promise(resolve => socket.listen(0, '127.0.0.1', resolve));
  const port = socket.address().port; await new Promise(resolve => socket.close(resolve));
  const log = fs.openSync(path.join(app, 'server.log'), 'w');
  const env = { ...process.env, NEXT_TELEMETRY_DISABLED: '1', NEXT_PUBLIC_BOUNDARY_MODE: mode };
  if (!development) {
    server = spawn(process.execPath, [require.resolve('next/dist/bin/next'), 'build', app, '--webpack'], {
    cwd: app, detached: true, stdio: ['ignore', log, log], env,
    });
    buildExit = await new Promise(resolve => server.once('exit', resolve));
    assert.equal(buildExit, 0, `${app}/server.log`);
  }
  server = spawn(process.execPath, [require.resolve('next/dist/bin/next'), development ? 'dev' : 'start', app,
    ...(development ? ['--webpack'] : []), '--hostname', '127.0.0.1', '--port', String(port)], {
    cwd: app, detached: true, stdio: ['ignore', log, log], env,
  }); fs.closeSync(log);
  const origin = `http://127.0.0.1:${port}`;
  for (let i = 0; i < 120; i++) {
    if (server.exitCode !== null) throw new Error(`Next exit ${server.exitCode}: ${app}/server.log`);
    try { if ((await fetch(origin + '/A')).ok) break; } catch { /* The owned server may not be listening yet. */ }
    if (i === 119) throw new Error('Next startup timeout');
    await new Promise(resolve => setTimeout(resolve, 500));
  }
  if (development) {
    for (const route of ['B', 'C', 'D', 'E', 'prime', 'unknown', 'recovered', 'later', 'blocked', 'allowed', 'test-page']) {
      assert.equal((await fetch(origin + '/' + route)).status, 200);
    }
  }
  browser = await webkit.launch({ headless: true });
  const page = await browser.newPage(); page.setDefaultTimeout(10000);
  await page.addInitScript(() => { sessionStorage.setItem('auth_header', 'Bearer synthetic-local-fixture'); });
  await page.route('**/*', route => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
  const errors = []; page.on('pageerror', error => errors.push(error.message));
  let accept = false, confirms = 0;
  page.on('dialog', async dialog => { confirms++; await (accept ? dialog.accept() : dialog.dismiss()); });
  const editor = page.getByRole('textbox', { name: 'draft' });
  const alert = page.getByRole('alert').filter({ hasText: '編集内容を保持して移動を停止' });
  const ready = () => page.waitForFunction(() => window.driver?.ready && window.driver.idle());
  const fresh = async () => {
    accept = true; await page.goto(origin + '/A'); await ready(); accept = false;
    if (development) {
      const replay = await page.evaluate(() => window.effectReplay);
      assert.ok(replay.setup >= 2 && replay.cleanup >= 1, 'actual development StrictMode replays effects');
    }
  };
  const choose = async value => { await page.evaluate(value => window.driver.mode(value), value); await page.waitForTimeout(50); };
  const push = as => page.evaluate(as => window.driver.push(as), as);
  const snapshot = () => page.evaluate(() => window.driver.snapshot());
  const settle = async as => { await page.waitForURL(origin + as); await page.waitForTimeout(150); };
  if (probe) {
    for (const guard of mode === 'bare' ? ['none'] : ['none', 'h1', 'menu']) {
      await fresh(); await choose(guard);
      await push('/prime'); await page.evaluate(() => history.back()); await settle('/A');
      await page.evaluate(() => window.scrollTo(0, 120));
      const initial = await snapshot(); await push('/B'); await push('/A');
      await page.evaluate(() => { window.scrollTo(0, 380); window.driver.clearTrace(); });
      await page.evaluate(() => history.go(-2)); await page.waitForTimeout(350);
      const result = await snapshot();
      observations.push({ guard, events: result.events, scrollY: result.scrollY, scrolls: result.scrolls,
        internalKeyMatchesTarget: result.routerKey === initial.state.key, targetKey: initial.state.key, internalKey: result.routerKey,
        route: result.route, query: result.query });
      assert.equal(result.path, '/A');
      if (!expectRegression) {
        assert.deepEqual(result.events, ['start:/A', 'complete:/A']);
        assert.equal(result.scrollY, 120);
        assert.deepEqual(result.scrolls, [[0, 120]]);
        assert.equal(result.routerKey, initial.state.key);
        assert.equal(result.route, '/A'); assert.deepEqual(result.query, {});
      } else {
        assert.deepEqual(result.events, []);
        assert.equal(result.scrollY, 380); assert.deepEqual(result.scrolls, []);
        assert.notEqual(result.routerKey, initial.state.key);
      }
    }
    outcomes.push('measured actual Next clean same-URL events, scroll restoration and internal key for each listed guard');
  } else if (before) {
    await fresh(); await choose('h1'); await page.evaluate(() => window.driver.seedUnknown(null));
    await editor.fill('preserved null draft'); await page.evaluate(() => history.back()); await settle('/unknown');
    assert.ok((await snapshot()).state?.sawa_navigation); assert.equal(await alert.count(), 0);
    assert.equal(await editor.inputValue(), 'preserved null draft');
    outcomes.push('REPRODUCED original Hospital null pop old-position stamp before beforePopState');
  } else {
    for (const guard of ['h1', 'menu']) {
      for (const state of [null, { unrelated: true }, { __NA: true }, { __N: true, url: '/[[...path]]', as: '/unknown', options: {} }]) {
        await fresh(); await choose(guard); await page.evaluate(state => window.driver.seedUnknown(state), state);
        await editor.fill('preserved unknown draft'); await page.evaluate(() => window.driver.beginLease());
        const count = (await snapshot()).length;
        await page.evaluate(() => history.back()); await settle('/unknown'); await alert.waitFor();
        assert.equal(await editor.inputValue(), 'preserved unknown draft'); assert.deepEqual((await snapshot()).state, state);
        assert.equal((await snapshot()).length, count); assert.equal((await snapshot()).lease, true);
        assert.equal(await page.evaluate(() => window.driver.allowedPush('/blocked')), false);
        if (guard === 'h1' && state === null) await page.screenshot({ path: path.join(proof, 'unknown-stop.png') });
        await choose('none'); assert.equal(await alert.count(), 0, 'unregister still clears blocked state');
        assert.equal(await page.evaluate(() => window.driver.allow('/recovered')), true, 'unregister clears original allow blocker');
        const recovery = await page.evaluate(() => window.driver.allowedPush('/recovered'));
        assert.equal(recovery, true, JSON.stringify(await snapshot()));
        outcomes.push(`${guard}: ${JSON.stringify(state)} stops without losing input/lease, unregister recovers`);
      }
      await fresh(); await choose(guard); await push('/B'); await push('/C'); await editor.fill('owned draft');
      const c = await snapshot(), start = confirms;
      await page.evaluate(() => history.back()); await settle('/C'); assert.deepEqual((await snapshot()).state, c.state);
      accept = true; await page.evaluate(() => history.back()); await settle('/B');
      const b = await snapshot(); accept = false;
      await page.evaluate(() => history.forward()); await settle('/B'); assert.deepEqual((await snapshot()).state, b.state);
      accept = true; await page.evaluate(() => history.forward()); await settle('/C');
      assert.equal(confirms, start + 4); assert.equal(await editor.inputValue(), 'owned draft');
      assert.equal((await snapshot()).length, c.length);
      outcomes.push(`${guard}: A-B-C both-direction cancel/approve once with intact history/input`);
      await page.reload(); await ready(); await choose(guard); await editor.fill('reload draft'); accept = false;
      const reloaded = await snapshot(); await page.evaluate(() => history.back()); await settle('/C');
      assert.deepEqual((await snapshot()).state, reloaded.state); assert.equal(await editor.inputValue(), 'reload draft');
      await page.evaluate(() => window.remountBoundary()); await page.waitForTimeout(100); await choose(guard);
      await editor.fill('remounted draft'); const remount = await snapshot();
      await page.evaluate(() => history.back()); await settle('/C'); assert.deepEqual((await snapshot()).state, remount.state);
      const commandStart = confirms;
      assert.equal(await page.evaluate(() => window.driver.allowedPush('/D')), false);
      assert.equal(confirms, commandStart + 1, 'allow entry still prompts after restored SSR entry');
      accept = true; assert.equal(await page.evaluate(() => window.driver.allowedPush('/D')), true); await settle('/D');
      assert.equal(confirms, commandStart + 2, 'allow then actual Next route must not double-confirm');
      outcomes.push(`${guard}: reload/StrictMode/remount, latest router and original allow entry retain exact confirmation count`);
      for (const phase of ['SSR', 'routed']) {
      await fresh(); await choose(guard);
      if (phase === 'routed') { await push('/prime'); await page.evaluate(() => history.back()); await settle('/A'); }
      await page.evaluate(() => history.pushState({ ...history.state }, '', location.href));
      await editor.fill('same URL draft'); accept = false; const same = await snapshot(), sameStart = confirms;
      await page.evaluate(() => history.back()); await page.waitForTimeout(250);
      assert.deepEqual((await snapshot()).state, same.state); assert.equal(confirms, sameStart + 1);
      accept = true; await page.evaluate(() => history.back()); await page.waitForTimeout(150);
      assert.equal((await snapshot()).state.sawa_navigation.index, same.state.sawa_navigation.index - 1);
      assert.equal(confirms, sameStart + 2); accept = false;
      assert.equal(await page.evaluate(() => window.driver.allowedPush('/later')), false); assert.equal(confirms, sameStart + 3);
      outcomes.push(`${guard}: ${phase} same-URL distinct indices cancel/approve without later allow-approval leak`);
      }
      await fresh(); await choose(guard); await push('/B'); await push('/C'); await editor.fill('rapid draft');
      const rapid = await snapshot(); await page.evaluate(() => { history.back(); history.back(); }); await page.waitForTimeout(500);
      assert.equal(await editor.inputValue(), 'rapid draft'); assert.equal((await snapshot()).length, rapid.length);
      assert.ok(await alert.count() || (await snapshot()).path === '/C');
      outcomes.push(`${guard}: rapid restoration preserves input/history or explicitly stops ambiguity`);
      await fresh(); await choose(guard); await editor.fill('same screen draft');
      const sameScreenStart = confirms;
      const sameScreen = guard === 'h1' ? '/allowed' : '/menu-masters';
      assert.equal(await page.evaluate(as => window.driver.allowedPush(as), sameScreen), true);
      await settle(sameScreen); assert.equal(confirms, sameScreenStart);
      assert.equal(await editor.inputValue(), 'same screen draft');
      outcomes.push(`${guard}: existing same-screen/path allowance remains prompt-free and retains input`);
      await page.getByRole('button', { name: 'save', exact: true }).click();
      const replacing = await snapshot();
      assert.equal(await page.evaluate(() => window.driver.replace('/E')), true); await settle('/E');
      assert.deepEqual((await snapshot()).state.sawa_navigation, replacing.state.sawa_navigation);
      assert.equal((await snapshot()).length, replacing.length);
      outcomes.push(`${guard}: ordinary clean replace retains owned index and history length`);
    }
    for (const state of [null, { unrelated: true }, { __NA: true }]) {
      await fresh(); await choose('h1'); await page.evaluate(state => window.driver.seedUnknown(state), state);
      await page.evaluate(() => history.back()); await settle('/unknown'); await ready();
      const result = await snapshot(); assert.equal(await alert.count(), 0);
      if (state === null) { assert.equal(result.state.__N, true); assert.equal(result.state.sawa_navigation, undefined); }
      else if (state.__NA) assert.equal(await page.evaluate(() => performance.getEntriesByType('navigation')[0].type), 'reload');
      else assert.deepEqual(result.state, state);
      outcomes.push(`clean ${JSON.stringify(state)} preserves original Next behavior`);
    }
  }
  assert.deepEqual(errors, []);
  fs.writeFileSync(path.join(proof, `${mode}-${development ? 'development-' : ''}${probe ? 'clean-probe' : before ? 'before' : 'final'}.json`), JSON.stringify({
    mode, app, port, runtime: development ? 'next dev' : 'next build/start', buildExit, outcomes, observations, errors, next: require('next/package.json').version,
    scope: 'Actual installed Hospital Next/React/hooks and WebKit; synthetic editor, not stg/business screen proof',
  }, null, 2));
  console.log(JSON.stringify({ mode, groups: outcomes.length, outcomes, observations, app }));
}
main().catch(error => { console.error(error); process.exitCode = 1; }).finally(async () => {
  if (browser) await browser.close();
  if (server && server.exitCode === null) { const closed = new Promise(resolve => server.once('exit', resolve)); process.kill(-server.pid, 'SIGTERM'); await closed; }
  if (fs.existsSync(path.join(app, 'node_modules'))) fs.unlinkSync(path.join(app, 'node_modules'));
  fs.writeFileSync(path.join(app, 'cleanup.json'), JSON.stringify({ browserClosed: !browser || !browser.isConnected(),
    serverPid: server?.pid, serverExited: !server || server.exitCode !== null || server.signalCode !== null,
    exit: server?.exitCode, signal: server?.signalCode, linkRemoved: !fs.existsSync(path.join(app, 'node_modules')) }, null, 2));
});
