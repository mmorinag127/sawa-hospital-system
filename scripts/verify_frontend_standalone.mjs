// Packaging smoke only; the upstream is an owned local fixture, not live auth proof.
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {readFile, readdir} from 'node:fs/promises';
import {createHash} from 'node:crypto';

const upstream = createServer((request, response) => {
  assert.equal(request.headers.authorization, 'Bearer standalone-auth-fixture');
  assert.equal(request.url, '/orders?standalone_probe=1');
  response.writeHead(401, {'Content-Type': 'application/json', 'X-Standalone-Probe': 'owned-fixture'});
  response.end(JSON.stringify({detail: 'fixture authorization rejected'}));
});
upstream.listen(0, '127.0.0.1');
await once(upstream, 'listening');
const reservation = createServer();
reservation.listen(0, '127.0.0.1');
await once(reservation, 'listening');
const port = reservation.address().port;
await new Promise(resolve => reservation.close(resolve));
const child = spawn(process.execPath, ['/app/server.js'], {cwd: '/app',
  env: {...process.env, HOSTNAME: '127.0.0.1', PORT: String(port),
    API_PROXY_TARGET: `http://127.0.0.1:${upstream.address().port}`}, stdio: 'ignore'});
const base = `http://127.0.0.1:${port}`;
const request = path => fetch(base + path, {signal: AbortSignal.timeout(10000)});
try {
  let ready = false;
  for (let attempt = 0; attempt < 100; attempt++) {
    if (child.exitCode !== null) throw new Error('standalone server exited before ready');
    try {
      const response = await request('/login');
      assert.equal(response.status, 200);
      assert.match(await response.text(), /<html/);
      ready = true;
      break;
    } catch {
      await new Promise(resolve => setTimeout(resolve, 100));
    }
  }
  assert.equal(ready, true, 'standalone login did not become ready');
  const files = await readdir('/app/.next/static/chunks', {recursive: true});
  const script = files.find(file => file.endsWith('.js'));
  assert.ok(script, 'standalone static chunk missing');
  const response = await request('/_next/static/chunks/' + script);
  assert.equal(response.status, 200);
  const actual = Buffer.from(await response.arrayBuffer());
  const expected = await readFile('/app/.next/static/chunks/' + script);
  assert.equal(createHash('sha256').update(actual).digest('hex'),
    createHash('sha256').update(expected).digest('hex'));
  assert.ok((await readdir('/app/public')).length, 'public assets missing');
  const rejected = await fetch(base + '/api/orders?standalone_probe=1', {
    headers: {Authorization: 'Bearer standalone-auth-fixture'}, signal: AbortSignal.timeout(10000)});
  assert.equal(rejected.status, 401);
  assert.equal(rejected.headers.get('x-standalone-probe'), 'owned-fixture');
  assert.deepEqual(await rejected.json(), {detail: 'fixture authorization rejected'});
  process.stdout.write('standalone login/static digest/proxy rejection: PASS (owned upstream)\n');
} finally {
  if (child.exitCode === null) {
    child.kill('SIGTERM');
    const exited = await Promise.race([once(child, 'exit').then(() => true),
      new Promise(resolve => setTimeout(() => resolve(false), 5000))]);
    if (!exited) {child.kill('SIGKILL'); await once(child, 'exit');}
  }
  await new Promise(resolve => upstream.close(resolve));
}
