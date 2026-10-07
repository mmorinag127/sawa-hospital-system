const fs = require('node:fs');
const path = require('node:path');
const cp = require('node:child_process');
const assert = require('node:assert/strict');
const { createHash } = require('node:crypto');
const root = path.resolve(__dirname, '..');
const frontend = path.join(root, 'frontend');
const { values } = require('node:util').parseArgs({ options: {
  'old-sharp': { type: 'string' }, 'baseline-sha': { type: 'string' }, 'output-dir': { type: 'string' },
} });
assert.ok(values['old-sharp'] && path.isAbsolute(values['old-sharp']));
assert.match(values['baseline-sha'] || '', /^[0-9a-f]{40}$/);
assert.ok(values['output-dir'] && path.isAbsolute(values['output-dir']));
const out = path.resolve(values['output-dir']);
const owned = fs.realpathSync(path.join(root, 'tmp'));
assert.ok(fs.realpathSync(path.dirname(out)).startsWith(owned + path.sep));
assert.ok(!fs.existsSync(out));
fs.mkdirSync(out);
for (const dir of ['home', 'temp', 'cache']) fs.mkdirSync(path.join(out, dir));
const hash = data => createHash('sha256').update(data).digest('hex');
const reference = fs.realpathSync(values['old-sharp']);
const old = { versions: { sharp: JSON.parse(fs.readFileSync(path.join(reference, 'package.json'))).version } };
const patched = require(path.join(frontend, 'node_modules/sharp'));
const npm = '/Users/mmorinag/.anyenv/envs/nodenv/versions/24.6.0/lib/node_modules/npm/bin/npm-cli.js';
async function main() {
  assert.equal(old.versions.sharp, '0.35.4');
  assert.equal(patched.versions.sharp, '0.35.5');
  const before = JSON.parse(cp.execFileSync('git', ['show', `${values['baseline-sha']}:frontend/package-lock.json`], { cwd: root }));
  assert.equal(before.packages['node_modules/sharp'].version, '0.35.4');
  const after = JSON.parse(fs.readFileSync(path.join(frontend, 'package-lock.json')));
  const changed = [];
  for (const key of new Set([...Object.keys(before.packages), ...Object.keys(after.packages)])) {
    if (JSON.stringify(before.packages[key]) !== JSON.stringify(after.packages[key])) {
      assert.match(key, /^node_modules\/(sharp|@img\/sharp-[^/]+)$/);
      changed.push({ key, before: before.packages[key], after: after.packages[key] });
    }
  }
  const native = Object.entries(after.packages).filter(([key]) => key.startsWith('node_modules/@img/sharp-'));
  for (const [key, value] of native) {
    assert.equal(value.version, key.includes('libvips') ? '1.3.4' : '0.35.5');
    assert.ok(value.integrity && value.resolved);
  }
  const assets = ['backend/src/data/templates/fax_fac00002_v1.png', 'docs/manuals/daily_flowchart.svg'];
  const parity = [];
  for (const asset of assets) {
    const input = fs.readFileSync(path.join(root, asset));
    for (const operation of ['decode', 'resize-png', 'resize-jpeg']) {
      async function process(sharp) {
        const module = sharp === old ? reference : path.join(frontend, 'node_modules/sharp');
        const decoded = JSON.parse(cp.execFileSync(global.process.execPath,
          [path.join(root, 'scripts/verify_sharp_image_parity.cjs'), '--image', module, path.join(root, asset), operation],
          { encoding: 'utf8', maxBuffer: 64 * 1024 * 1024 }));
        assert.equal(decoded.versions.sharp, sharp.versions.sharp);
        return { info: decoded.info, data: Buffer.from(decoded.raw, 'base64'),
          encoded: decoded.encoded ? Buffer.from(decoded.encoded, 'base64') : null };
      }
      const a = await process(old), b = await process(patched);
      assert.deepEqual(a.info, b.info);
      assert.ok(a.data.equals(b.data), `${asset} ${operation} decoded pixels differ`);
      if (a.encoded) assert.ok(a.encoded.equals(b.encoded), `${asset} ${operation} output differs`);
      parity.push({ asset, inputSha256: hash(input), operation, dimensions: b.info,
                    rawSha256: hash(b.data), encodedSha256: b.encoded ? hash(b.encoded) : null });
    }
  }
  const env = { PATH: `${path.dirname(process.execPath)}:/usr/bin:/bin`, HOME: path.join(out, 'home'),
    TMPDIR: path.join(out, 'temp'), npm_config_cache: path.join(out, 'cache') };
  const audits = [];
  for (const suffix of [[], ['--omit=dev']]) {
    const args = [npm, 'audit', '--json', ...suffix];
    const result = cp.spawnSync(process.execPath, args, { cwd: frontend, env, encoding: 'utf8' });
    if (result.error) throw result.error;
    const report = JSON.parse(result.stdout);
    assert.ok(report.metadata && report.vulnerabilities, 'audit did not return a complete report');
    assert.ok(!report.vulnerabilities.sharp, 'sharp advisory remains');
    const file = path.join(out, suffix.length ? 'audit-production.json' : 'audit-full.json');
    fs.writeFileSync(file, result.stdout, { flag: 'wx' });
    audits.push({ command: [process.execPath, ...args], exit: result.status, file,
      sha256: hash(result.stdout), counts: report.metadata.vulnerabilities });
  }
  const report = { base: cp.execFileSync('git', ['rev-parse', 'HEAD'], { cwd: root, encoding: 'utf8' }).trim(),
    node: process.version, platform: process.platform, arch: process.arch,
    baselineSha: values['baseline-sha'], command: [process.execPath, ...process.argv.slice(1)],
    referencePath: reference, referencePackageSha256: hash(fs.readFileSync(path.join(reference, 'package.json'))),
    referenceNativeSha256: hash(fs.readFileSync(path.join(reference, '../@img/sharp-darwin-arm64/lib/sharp-darwin-arm64-0.35.4.node'))),
    oldVersions: old.versions, patchedVersions: patched.versions, changed, native, parity, audits,
    sourceHashes: Object.fromEntries(['frontend/package.json', 'frontend/package-lock.json', 'scripts/verify_sharp_patch.cjs'].map(file => [file, hash(fs.readFileSync(path.join(root, file)))])),
    limitation: 'No repository JPEG input exists. JPEG output checks do not prove independent JPEG input parity. Linux lock records only; macOS native runtime. No frontend build or backend recognizer proof.' };
  const file = path.join(out, 'verification.json');
  fs.writeFileSync(file, JSON.stringify(report, null, 2) + '\n', { flag: 'wx' });
  console.log(JSON.stringify({ file, sha256: hash(fs.readFileSync(file)), parityCases: parity.length, audits }));
}
main().catch(error => { console.error(error); process.exitCode = 1; });
