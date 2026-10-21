'use strict';
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const cp = require('node:child_process');
const { AsyncLocalStorage } = require('node:async_hooks');
const { fileURLToPath } = require('node:url');
const { syncBuiltinESMExports } = require('node:module');

const read = fs.readFileSync.bind(fs);
const write = fs.writeFileSync.bind(fs);
const root = fs.realpathSync(process.env.SAWA_IMPACT_ROOT);
const state = path.resolve(process.env.SAWA_NODE_IMPACT_STATE);
const current = path.resolve(process.argv[1] || root);
const local = path.relative(root, current);
const tracked = new Set(cp.execFileSync('git', ['ls-files', '-z'], { cwd: root }).toString().split('\0').filter(Boolean));
const hash = (value) => crypto.createHash('sha256').update(value).digest('hex');
let busy = false;
const fileHash = (file) => {
  const wasBusy = busy;
  busy = true;
  try { return hash(read(file)); } catch { return null; } finally { busy = wasBusy; }
};
const volatile = new Set(['HOME', 'PWD', 'OLDPWD', 'PATH', 'TMP', 'TEMP', 'TMPDIR', 'SHLVL', '_',
  'NODE_OPTIONS', 'NODE_TEST_CONTEXT', 'SAWA_IMPACT_ROOT', 'SAWA_NODE_IMPACT_STATE', 'SAWA_IMPACT_FULL']);
const env = Object.fromEntries(Object.entries(process.env).filter(([key]) =>
  !volatile.has(key) && !/^(GITHUB_|RUNNER_|ACTIONS_|INPUT_)/.test(key)).sort());
const configs = Object.fromEntries([...tracked].sort().filter((name) =>
  /\.(toml|ini|cfg|ya?ml|lock)$/.test(name) ||
  /(^|\/)(package(-lock)?|tsconfig[^/]*)\.json$/.test(name) || name.startsWith('infra/ci/'))
  .map((name) => [name, fileHash(path.join(root, name))]));
const context = hash(JSON.stringify({ version: 1, node: process.version, platform: process.platform,
  tracked: [...tracked].sort(), configs, env }));
// Diagnose cache misses without persisting environment values or changing selection.
const contextParts = Object.fromEntries(Object.entries({ node: process.version, platform: process.platform,
  tracked: [...tracked].sort(), configs, environment: env }).map(([key, value]) => [key, hash(JSON.stringify(value))]));
const environmentWithout = Object.fromEntries(Object.keys(env).map((excluded) =>
  [excluded, hash(JSON.stringify(Object.fromEntries(Object.entries(env).filter(([key]) => key !== excluded))))]));
const destination = path.join(state, `${hash(local)}.json`);
let previous = {};
let previousContextMatches = false;
let previousParts = {};
let previousEnvironmentWithout = {};
try {
  const saved = JSON.parse(read(destination, 'utf8'));
  previousParts = saved.context_parts || {};
  previousEnvironmentWithout = saved.environment_without || {};
  previousContextMatches = saved.context === context;
  if (saved.context === context && saved.tests && typeof saved.tests === 'object') previous = saved.tests;
} catch { /* Missing/corrupt dependency state executes tests. */ }
if (process.env.SAWA_IMPACT_FULL === '1') previous = {};

const storage = new AsyncLocalStorage();
const globalInputs = {};
const globalUnsafe = new Set();
const records = new Map();
const counts = new Map();
const report = { selected: [], reused: [], reasons: {}, durations_seconds: {}, untracked: {},
  previous_context_matches: previousContextMatches, previous_count: Object.keys(previous).length,
  changed_context_parts: Object.keys(contextParts).filter((key) => previousParts[key] !== contextParts[key]),
  environment_keys: Object.keys(env),
  single_environment_difference: Object.keys(environmentWithout).filter((key) =>
    previousParts.environment !== contextParts.environment && previousEnvironmentWithout[key] === environmentWithout[key]) };
let used = false;

function unsafe(reason) {
  (storage.getStore()?.unsafe || globalUnsafe).add(reason);
}

function dependency(value) {
  // An unsafe record always executes; hashing more inputs cannot make it reusable.
  if (busy || globalUnsafe.size || storage.getStore()?.unsafe.size) return;
  busy = true;
  try {
    let file;
    try {
      file = path.resolve(value instanceof URL ? fileURLToPath(value) : String(value));
    } catch { unsafe('unresolved-file'); return; }
    if (file === state || file.startsWith(state + path.sep)) return;
    if (file.startsWith(__dirname + path.sep)) return;
    const name = path.relative(root, file);
    if (name.split(path.sep).includes('node_modules')) return; // Lockfile belongs to the context.
    if (tracked.has(name)) {
      const target = storage.getStore()?.inputs || globalInputs;
      const valueHash = fileHash(file);
      if (valueHash === null || (name in target && target[name] !== valueHash)) unsafe('changing-file');
      target[name] = valueHash;
    } else {
      unsafe('untracked-file');
    }
  } finally { busy = false; }
}

function wrapMethod(object, name, before) {
  const original = object[name];
  if (typeof original !== 'function') return;
  object[name] = function (...args) { before(...args); return Reflect.apply(original, this, args); };
}

for (const name of ['readFileSync', 'readFile', 'createReadStream']) wrapMethod(fs, name, dependency);
wrapMethod(fs.promises, 'readFile', dependency);
for (const object of [fs, fs.promises]) {
  for (const name of ['open', 'openSync']) wrapMethod(object, name, (file, flags = 'r') => {
    if (flags === 'r' || flags === 'rs' || flags === 0) dependency(file);
    else unsafe('file-write-or-unknown-open');
  });
  for (const name of ['readdir', 'readdirSync', 'opendir', 'opendirSync']) {
    wrapMethod(object, name, () => unsafe('directory-input'));
  }
  for (const name of ['stat', 'statSync', 'lstat', 'lstatSync', 'access', 'accessSync', 'exists', 'existsSync']) {
    wrapMethod(object, name, (file) => {
      if (!busy) {
        dependency(file);
        unsafe('file-metadata');
      }
    });
  }
  for (const name of ['writeFile', 'writeFileSync', 'appendFile', 'appendFileSync', 'unlink', 'unlinkSync',
    'rename', 'renameSync', 'rm', 'rmSync']) wrapMethod(object, name, () => unsafe('file-write'));
}
for (const name of ['spawn', 'spawnSync', 'exec', 'execSync', 'execFile', 'execFileSync', 'fork']) {
  wrapMethod(cp, name, () => unsafe('subprocess'));
}
wrapMethod(require('node:net').Socket.prototype, 'connect', () => unsafe('network'));
syncBuiltinESMExports();
const environment = process.env;
process.env = new Proxy(environment, {
  set(target, key, value) {
    target[key] = value;
    return true;
  },
  get(target, key) {
    if (!busy && !globalUnsafe.size && typeof key === 'string' &&
        (volatile.has(key) || /^(GITHUB_|RUNNER_|ACTIONS_|INPUT_)/.test(key))) {
      const caller = new Error().stack?.split('\n')[2] || '';
      if (!caller.includes('(node:') && !caller.includes('at node:')) {
        unsafe('volatile-environment');
      }
    }
    return Reflect.get(target, key);
  },
});

function valid(record) {
  return record && record.passed === true && Array.isArray(record.unsafe) && !record.unsafe.length &&
    record.inputs && typeof record.inputs === 'object' &&
    Object.entries(record.inputs).every(([name, value]) =>
      tracked.has(name) && typeof value === 'string' && value.length === 64 &&
      fileHash(path.join(root, name)) === value);
}

function wrapTest(original, forced = {}) {
  function wrapped(...args) {
    used = true;
    const callback = args.findLast((arg) => typeof arg === 'function');
    const name = typeof args[0] === 'string' ? args[0] : callback?.name || '<anonymous>';
    const options = { ...(args.find((arg) => arg && typeof arg === 'object') || {}), ...forced };
    const count = counts.get(name) || 0;
    counts.set(name, count + 1);
    const id = `${local}::${name}#${count}`;
    const old = previous[id];
    const reusable = callback && callback.length < 2 && !options.skip && !options.todo && !options.only &&
      !globalUnsafe.size && valid(old);
    if (reusable) {
      report.reasons[id] = 'unchanged-dependencies';
      report.reused.push(id);
      records.set(id, old);
      return original(name, { ...options, skip: 'unchanged dependencies (verified previous success)' }, callback);
    }
    report.selected.push(id);
    report.reasons[id] = globalUnsafe.size || old?.unsafe?.length ? 'untracked-input' :
      !old ? 'missing-baseline-or-full' : 'changed-dependency-or-failed-test';
    if (!callback) return original(...args);
    if (callback.length > 1) {
      globalUnsafe.add('callback-style');
      return original(...args);
    }
    const record = { inputs: {}, unsafe: new Set(), passed: false };
    records.set(id, record);
    return original(name, options, async function (t) {
      return storage.run(record, async () => {
        const started = process.hrtime.bigint();
        const target = new Proxy(t, { get(object, key) {
          const value = Reflect.get(object, key, object);
          if (['test', 'before', 'after', 'beforeEach', 'afterEach', 'skip', 'todo'].includes(key)) unsafe('nested-or-hook');
          return typeof value === 'function' ? value.bind(object) : value;
        } });
        try {
          await callback.call(this, target);
          record.passed = !options.skip && !options.todo;
        } finally {
          report.durations_seconds[id] = Number(process.hrtime.bigint() - started) / 1e9;
        }
      });
    });
  }
  wrapped.skip = wrapVariant(original, { skip: true });
  wrapped.todo = wrapVariant(original, { todo: true });
  wrapped.only = wrapVariant(original, { only: true });
  return wrapped;
}

function wrapVariant(original, options) {
  return (...args) => wrapTest(original, options)(...args);
}

function hook(original) {
  const wrapped = (...args) => { globalUnsafe.add('hook'); return original(...args); };
  for (const key of ['skip', 'only', 'todo']) {
    if (typeof original[key] === 'function') wrapped[key] = hook(original[key]);
  }
  return wrapped;
}

dependency(current);
process.on('exit', (code) => {
  if (!used) return;
  busy = true;
  fs.mkdirSync(state, { recursive: true });
  const tests = {};
  for (const [id, record] of records) {
    tests[id] = { passed: code === 0 && record.passed,
      inputs: { ...globalInputs, ...record.inputs },
      unsafe: [...new Set([...globalUnsafe, ...record.unsafe])] };
    if (tests[id].unsafe.length) report.untracked[id] = tests[id].unsafe;
  }
  write(destination, JSON.stringify({ context, tests, context_parts: contextParts, environment_without: environmentWithout }));
  write(destination + '.report.json', JSON.stringify(report, null, 2));
});

module.exports = { dependency, unsafe, wrapTest, hook };
