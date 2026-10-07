const fs = require('node:fs');
const path = require('node:path');
const cp = require('node:child_process');
const assert = require('node:assert/strict');
const { createHash } = require('node:crypto');
const root = path.resolve(__dirname, '..');
const hash = data => createHash('sha256').update(data).digest('hex');
async function main() {
  if (process.argv[2] === '--image') {
    const sharp = require(process.argv[3]);
    const input = fs.readFileSync(process.argv[4]);
    const operation = process.argv[5];
    let image = sharp(input);
    if (operation !== 'decode') image = image.resize({ width: 320 });
    if (operation === 'resize-png') image = image.png();
    if (operation === 'resize-jpeg') image = image.jpeg({ quality: 80 });
    let encoded;
    if (operation !== 'decode') {
      encoded = await image.toBuffer();
      image = sharp(encoded);
    }
    const { data, info } = await image.raw().toBuffer({ resolveWithObject: true });
    console.log(JSON.stringify({ versions: sharp.versions, info, raw: data.toString('base64'),
      encoded: encoded ? encoded.toString('base64') : null }));
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
  const patched = path.join(root, 'frontend/node_modules/sharp');
  const cases = [];
  for (const asset of ['backend/src/data/templates/fax_fac00002_v1.png', 'docs/manuals/daily_flowchart.svg']) {
    for (const operation of ['decode', 'resize-png', 'resize-jpeg']) {
      const commands = [reference, patched].map(module => [process.execPath, __filename, '--image', module, path.join(root, asset), operation]);
      const [a, b] = commands.map(command => JSON.parse(cp.execFileSync(command[0], command.slice(1), { encoding: 'utf8', maxBuffer: 64 * 1024 * 1024 })));
      assert.equal(a.versions.sharp, '0.35.4');
      assert.equal(b.versions.sharp, '0.35.5');
      assert.deepEqual(a.info, b.info);
      assert.ok(Buffer.from(a.raw, 'base64').equals(Buffer.from(b.raw, 'base64')));
      assert.equal(a.encoded, b.encoded);
      cases.push({ asset, operation, commands, info: b.info,
        inputSha256: hash(fs.readFileSync(path.join(root, asset))),
        rawSha256: hash(Buffer.from(b.raw, 'base64')),
        encodedSha256: b.encoded ? hash(Buffer.from(b.encoded, 'base64')) : null,
        referenceVersions: a.versions, patchedVersions: b.versions });
    }
  }
  const file = path.join(output, 'independent-parity.json');
  const report = { base: cp.execFileSync('git', ['rev-parse', 'HEAD'], { cwd: root, encoding: 'utf8' }).trim(),
    node: process.version, platform: process.platform, arch: process.arch, cases,
    baselineSha: values['baseline-sha'], command: [process.execPath, ...process.argv.slice(1)],
    referencePath: reference, referencePackageSha256: hash(fs.readFileSync(path.join(reference, 'package.json'))),
    referenceNativeSha256: hash(fs.readFileSync(path.join(reference, '../@img/sharp-darwin-arm64/lib/sharp-darwin-arm64-0.35.4.node'))),
    manifestSha256: hash(fs.readFileSync(path.join(root, 'frontend/package.json'))),
    lockSha256: hash(fs.readFileSync(path.join(root, 'frontend/package-lock.json'))),
    scriptSha256: hash(fs.readFileSync(__filename)),
    limitation: 'JPEG encoding checked; no fixed repository JPEG input found. Linux native execution not performed.' };
  fs.writeFileSync(file, JSON.stringify(report, null, 2) + '\n', { flag: 'wx' });
  console.log(JSON.stringify({ file, sha256: hash(fs.readFileSync(file)), cases: cases.length }));
}
main().catch(error => { console.error(error); process.exitCode = 1; });
