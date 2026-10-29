const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { execFileSync, spawnSync } = require('node:child_process');
const root = path.resolve(__dirname, '../..');
const frontend = path.join(root, 'frontend');

function localDependencies(directory) {
  const manifest = JSON.parse(fs.readFileSync(path.join(directory, 'package.json')));
  const lock = JSON.parse(fs.readFileSync(path.join(directory, 'package-lock.json')));
  const result = [];
  for (const section of ['dependencies', 'devDependencies', 'optionalDependencies']) {
    for (const [name, value] of Object.entries(manifest[section] || {})) {
      if (!value.startsWith('file:')) continue;
      assert.equal(lock.packages[''][section][name], value);
      assert.equal(lock.packages[`node_modules/${name}`].resolved, value);
      const relative = value.slice(5);
      assert.ok(relative.startsWith('vendor/'));
      assert.ok(fs.statSync(path.join(directory, relative)).isFile());
      result.push(relative);
    }
  }
  assert.ok(result.length > 0);
  return result;
}

test('deps stage copies every declared committed local dependency before npm ci', () => {
  const docker = fs.readFileSync(path.join(frontend, 'Dockerfile'), 'utf8');
  const deps = docker.split('FROM trusted-base AS builder')[0];
  assert.match(deps, /^COPY vendor \.\/vendor$/m);
  assert.ok(deps.indexOf('COPY vendor ./vendor') < deps.indexOf('RUN npm ci'));
  const old = execFileSync('git', ['show', '4a9beb430da25f878eb8a3bdd6e834298c803afe:frontend/Dockerfile'], { cwd: root, encoding: 'utf8' });
  assert.equal((old.match(/FROM node:20-slim/g) || []).length, 3);
  const trustedBase = [
    'FROM node:24-trixie-slim AS trusted-base',
    'RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates \\',
    '    && dpkg --compare-versions "$(dpkg-query -W -f=\'${Version}\' ca-certificates)" ge \'20250419+deb13u1\' \\',
    '    && rm -rf /var/lib/apt/lists/*',
    'ENV NODE_OPTIONS=--use-openssl-ca \\',
    '    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \\',
    '    SSL_CERT_DIR=/etc/ssl/certs',
    '', '',
  ].join('\n');
  const expected = trustedBase + old.replace(/^COPY vendor\/.*$/m, 'COPY vendor ./vendor')
    .replaceAll('FROM node:20-slim', 'FROM trusted-base')
    .replace('ARG NEXT_PUBLIC_DEPLOYED_AT=unknown\n', 'ARG NEXT_PUBLIC_DEPLOYED_AT=unknown\nARG NEXT_PUBLIC_SAWA_ASSISTANT_TRIAL_ENABLED=false\n')
    .replace('ENV NEXT_PUBLIC_PORTAL_URL=${NEXT_PUBLIC_PORTAL_URL}\n', 'ENV NEXT_PUBLIC_PORTAL_URL=${NEXT_PUBLIC_PORTAL_URL}\nENV NEXT_PUBLIC_SAWA_ASSISTANT_TRIAL_ENABLED=${NEXT_PUBLIC_SAWA_ASSISTANT_TRIAL_ENABLED}\n')
    .replace('ARG NEXT_PUBLIC_GIT_SHA=unknown\n', '')
    .replace('ARG NEXT_PUBLIC_DEPLOYED_AT=unknown\n', '')
    .replace('ENV NEXT_PUBLIC_GIT_SHA=${NEXT_PUBLIC_GIT_SHA}\n', '')
    .replace('ENV NEXT_PUBLIC_DEPLOYED_AT=${NEXT_PUBLIC_DEPLOYED_AT}\n', '')
    .replace('FROM trusted-base AS runner\nWORKDIR /app\n',
      'FROM trusted-base AS runner\nWORKDIR /app\nARG NEXT_PUBLIC_GIT_SHA=unknown\nARG NEXT_PUBLIC_DEPLOYED_AT=unknown\nENV SAWA_GIT_SHA=${NEXT_PUBLIC_GIT_SHA}\nENV SAWA_DEPLOYED_AT=${NEXT_PUBLIC_DEPLOYED_AT}\n')
    .replace('COPY --from=builder /app/package.json ./package.json\nCOPY --from=builder /app/next.config.js ./next.config.js',
      'COPY --from=builder /app/.next/standalone ./')
    .replace('COPY --from=builder /app/.next ./.next\nCOPY --from=builder /app/node_modules ./node_modules\nCMD ["npm", "run", "start"]',
      'COPY --from=builder /app/.next/static ./.next/static\nCOPY --from=builder /app/node_modules/@sawa/ui ./node_modules/@sawa/ui\nCMD ["node", "server.js"]');
  assert.equal(docker, expected);
  for (const relative of localDependencies(frontend)) {
    const committed = execFileSync('git', ['show', `HEAD:frontend/${relative}`], { cwd: root });
    assert.deepEqual(fs.readFileSync(path.join(frontend, relative)), committed);
  }
  const ignored = fs.readFileSync(path.join(frontend, '.dockerignore'), 'utf8').split(/\r?\n/);
  assert.ok(!ignored.some(line => /vendor|\.tgz|^\*$/.test(line)));
});

test('real preparation retains local dependency bytes and existing source checks reject mutations', () => {
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'hospital-docker-vendor-'));
  try {
    const prepared = execFileSync('bash', [path.join(root, 'scripts/prepare_web_deploy_source.sh'), 'vendor-contract', path.join(temporary, 'prepared')], { cwd: root, encoding: 'utf8' }).trim();
    for (const relative of localDependencies(prepared)) {
      assert.deepEqual(fs.readFileSync(path.join(prepared, relative)), fs.readFileSync(path.join(frontend, relative)));
    }
    const validator = path.join(root, 'scripts/validate_web_deploy_source.sh');
    execFileSync('bash', [validator, prepared], { cwd: root });
    const sentinel = path.join(prepared, 'src/pages/orders/index.tsx');
    const original = fs.readFileSync(sentinel);
    fs.appendFileSync(sentinel, '\n// disposable mutation\n');
    const rejected = spawnSync('bash', [validator, prepared], { cwd: root, encoding: 'utf8' });
    assert.notEqual(rejected.status, 0);
    assert.match(rejected.stderr, /deploy copy changed after preparation/);
    fs.writeFileSync(sentinel, original);
    execFileSync('bash', [validator, prepared], { cwd: root });
  } finally {
    fs.rmSync(temporary, { recursive: true, force: true });
  }
});
