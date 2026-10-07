const fs = require('node:fs');
const path = require('node:path');
const cp = require('node:child_process');
const assert = require('node:assert/strict');
const { createHash } = require('node:crypto');
const root = path.resolve(__dirname, '..');
const hash = data => createHash('sha256').update(data).digest('hex');
async function main() {
  if (process.argv[2] === '--fixture') {
    assert.ok(path.isAbsolute(process.argv[4]));
    assert.ok(fs.realpathSync(path.dirname(process.argv[4])).startsWith(fs.realpathSync(path.join(root, 'tmp')) + path.sep));
    const reference = process.argv[5];
    const sharp = require(reference);
    assert.equal(sharp.versions.sharp, '0.35.4');
    const settings = { quality: 80, chromaSubsampling: '4:2:0', progressive: false, mozjpeg: false };
    const data = await sharp(fs.readFileSync(process.argv[3])).jpeg(settings).toBuffer();
    fs.writeFileSync(process.argv[4], data, { flag: 'wx' });
    console.log(JSON.stringify({ settings, versions: sharp.versions,
      encoderPackageSha256: hash(fs.readFileSync(path.join(reference, 'package.json'))),
      encoderSourceSha256: hash(fs.readFileSync(path.join(reference, 'dist/output.cjs'))),
      encoderNativeSha256: hash(fs.readFileSync(path.join(reference, '../@img/sharp-darwin-arm64/lib/sharp-darwin-arm64-0.35.4.node'))),
      fixtureSha256: hash(data) }));
    return;
  }
  const { values } = require('node:util').parseArgs({ options: {
    'old-sharp': { type: 'string' }, 'baseline-sha': { type: 'string' }, 'output-dir': { type: 'string' },
  } });
  assert.ok(values['old-sharp'] && path.isAbsolute(values['old-sharp']));
  assert.match(values['baseline-sha'] || '', /^[0-9a-f]{40}$/);
  assert.ok(values['output-dir'] && path.isAbsolute(values['output-dir']));
  const output = path.resolve(values['output-dir']);
  const owned = fs.realpathSync(path.join(root, 'tmp'));
  assert.ok(fs.realpathSync(path.dirname(output)).startsWith(owned + path.sep));
  assert.ok(!fs.existsSync(output));
  fs.mkdirSync(output);
  const reference = fs.realpathSync(values['old-sharp']);
  assert.equal(JSON.parse(fs.readFileSync(path.join(reference, 'package.json'))).version, '0.35.4');
  assert.equal(JSON.parse(cp.execFileSync('git', ['show', `${values['baseline-sha']}:frontend/package-lock.json`], { cwd: root })).packages['node_modules/sharp'].version, '0.35.4');
  const source = path.join(root, 'backend/src/data/templates/fax_fac00002_v1.png');
  const fixture = path.join(output, 'derived-baseline-quality80-420-final.jpg');
  const fixtureCommand = [process.execPath, __filename, '--fixture', source, fixture, reference];
  const provenance = JSON.parse(cp.execFileSync(fixtureCommand[0], fixtureCommand.slice(1), { encoding: 'utf8' }));
  const imageScript = path.join(root, 'scripts/verify_sharp_image_parity.cjs');
  const cases = [];
  for (const operation of ['decode', 'resize-png', 'resize-jpeg']) {
    const commands = [reference, path.join(root, 'frontend/node_modules/sharp')].map(module =>
      [process.execPath, imageScript, '--image', module, fixture, operation]);
    const [a, b] = commands.map(command => JSON.parse(cp.execFileSync(command[0], command.slice(1),
      { encoding: 'utf8', maxBuffer: 64 * 1024 * 1024 })));
    assert.equal(a.versions.sharp, '0.35.4');
    assert.equal(b.versions.sharp, '0.35.5');
    assert.deepEqual(a.info, b.info);
    assert.ok(Buffer.from(a.raw, 'base64').equals(Buffer.from(b.raw, 'base64')));
    assert.equal(a.encoded, b.encoded);
    cases.push({ operation, commands, dimensions: b.info, rawSha256: hash(Buffer.from(b.raw, 'base64')),
      encodedSha256: b.encoded ? hash(Buffer.from(b.encoded, 'base64')) : null,
      referenceVersions: a.versions, patchedVersions: b.versions });
  }
  const report = { base: cp.execFileSync('git', ['rev-parse', 'HEAD'], { cwd: root, encoding: 'utf8' }).trim(),
    baselineSha: values['baseline-sha'], command: [process.execPath, ...process.argv.slice(1)],
    referencePath: reference, source, sourceSha256: hash(fs.readFileSync(source)), fixture, fixtureCommand, provenance, cases,
    node: process.version, platform: process.platform, arch: process.arch,
    sourceHashes: Object.fromEntries([__filename, imageScript, path.join(root, 'frontend/package.json'),
      path.join(root, 'frontend/package-lock.json')].map(file => [file, hash(fs.readFileSync(file))])),
    scope: 'Synthetic PNG-derived JPEG codec component QA only; not customer JPEG, FAX/PDF/order-flow, quantity or Linux runtime proof.' };
  const file = path.join(output, 'jpeg-parity.json');
  fs.writeFileSync(file, JSON.stringify(report, null, 2) + '\n', { flag: 'wx' });
  console.log(JSON.stringify({ file, sha256: hash(fs.readFileSync(file)), provenance, cases: cases.length }));
}
main().catch(error => { console.error(error); process.exitCode = 1; });
