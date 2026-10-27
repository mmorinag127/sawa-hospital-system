'use strict';
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const cp = require('node:child_process');
const { AsyncLocalStorage } = require('node:async_hooks');
const { fileURLToPath } = require('node:url');
const { syncBuiltinESMExports } = require('node:module');
const { types } = require('node:util');
const NativeError = Error;

const read = fs.readFileSync.bind(fs);
const write = fs.writeFileSync.bind(fs);
const nativeExists = fs.existsSync.bind(fs);
const nativeStat = fs.statSync.bind(fs), nativeLstat = fs.lstatSync.bind(fs);
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
  'INVOCATION_ID', 'JOURNAL_STREAM', 'SYSTEMD_EXEC_PID', 'MEMORY_PRESSURE_WATCH',
  'MEMORY_PRESSURE_WRITE', 'NODE_TEST_WORKER_ID',
  'CODEX_TOOL_CALL_ID', 'PG_BIN', 'REFRESH_TEST_PG_BIN',
  'NODE_TEST_CONTEXT', 'SAWA_IMPACT_ROOT', 'SAWA_NODE_IMPACT_STATE', 'SAWA_IMPACT_FULL']);
const runnerEnvironmentKey = (key) => volatile.has(key) || /^(GITHUB_|RUNNER_|ACTIONS_|INPUT_|SAWA_LOCAL_)/.test(key);
const env = Object.fromEntries(Object.entries(process.env).filter(([key]) =>
  !runnerEnvironmentKey(key)).sort());
const configs = Object.fromEntries([...tracked].sort().filter((name) =>
  /\.(toml|ini|cfg|ya?ml|lock)$/.test(name) ||
  /(^|\/)(package(-lock)?|tsconfig[^/]*)\.json$/.test(name) || name.startsWith('infra/ci/'))
  .map((name) => [name, fileHash(path.join(root, name))]));
const trackerFiles = Object.fromEntries(['core.cjs', 'register.mjs', 'test.cjs']
  .map((name) => [name, fileHash(path.join(__dirname, name))]));
Object.assign(configs, Object.fromEntries(Object.entries(trackerFiles).map(([name, value]) =>
  ['node-impact-tracker:' + name, value])));
const context = hash(JSON.stringify({ version: 2, node: process.version, platform: process.platform, root, cwd: process.cwd(), arch: process.arch,
  tracked: [...tracked].sort(), configs, env }));
// Diagnose cache misses without persisting environment values or changing selection.
const contextParts = Object.fromEntries(Object.entries({ node: process.version, platform: process.platform, root, cwd: process.cwd(), arch: process.arch,
  tracked: [...tracked].sort(), configs, environment: env }).map(([key, value]) => [key, hash(JSON.stringify(value))]));
const destination = path.join(state, `${hash(local)}.json`);
let previous = {};
let previousContextMatches = false;
let previousParts = {};
try {
  const saved = JSON.parse(read(destination, 'utf8'));
  previousParts = saved.context_parts || {};
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
  changed_context_parts: Object.keys(contextParts).filter((key) => previousParts[key] !== contextParts[key]) };
let used = false;

function unsafe(reason) {
  (storage.getStore()?.unsafe || globalUnsafe).add(reason);
}

const statFields = new Set(['dev', 'ino', 'mode', 'nlink', 'uid', 'gid', 'rdev', 'size',
  'blksize', 'blocks', 'atimeMs', 'mtimeMs', 'ctimeMs', 'birthtimeMs',
  'atime', 'mtime', 'ctime', 'birthtime']);
const statValue = stats => Object.fromEntries([...statFields].map(key => [key, stats[key]]));
function rawPath(value) {
  try {
    let raw;
    if (typeof value === 'string') raw = value;
    else {
      if (!value || typeof value !== 'object' || types.isProxy(value)
          || Object.getPrototypeOf(value) !== URL.prototype
          || Object.values(Object.getOwnPropertyDescriptors(value)).some(item => item.get || item.set)) return null;
      raw = fileURLToPath(value);
    }
    return raw.split(path.sep).includes('..') ? null : raw;
  } catch { return null; }
}
function observedPath(value) {
  try {
    const raw = rawPath(value);
    if (raw === null) return null;
    const file = path.resolve(raw);
    const name = path.relative(root, file);
    if (!name || name === '..' || name.startsWith('..' + path.sep) || path.isAbsolute(name)) return null;
    return {file, name};
  } catch { return null; }
}
function observation(name, value) {
  if (busy || globalUnsafe.size || storage.getStore()?.unsafe.size) return;
  const target = storage.getStore()?.inputs || globalInputs;
  const fingerprint = hash(JSON.stringify(value));
  if (name in target && target[name] !== fingerprint) unsafe('changing-metadata');
  target[name] = fingerprint;
}
function observationHash(name) {
  const [kind, key, ...parts] = name.split(':');
  const relative = parts.join(':');
  const item = observedPath(path.join(root, relative));
  if (!relative || !item || item.name !== relative) return null;
  try {
    if (kind === 'exists' && key === 'value') return hash(JSON.stringify(nativeExists(item.file)));
    if (!['stat', 'lstat'].includes(kind) || key !== 'all') return null;
    const stats = (kind === 'stat' ? nativeStat : nativeLstat)(item.file);
    return hash(JSON.stringify(statValue(stats)));
  } catch { return null; }
}

function dependency(value) {
  // An unsafe record always executes; hashing more inputs cannot make it reusable.
  if (busy || globalUnsafe.size || storage.getStore()?.unsafe.size) return;
  busy = true;
  try {
    let file;
    try {
      const raw = rawPath(value);
      if (raw === null) { unsafe('unresolved-file'); return; }
      file = path.resolve(raw);
    } catch { unsafe('unresolved-file'); return; }
    if (file === state || file.startsWith(state + path.sep)) return;
    if (['core.cjs', 'register.mjs', 'test.cjs'].some(name => file === path.join(__dirname, name))) return;
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

let pathReadSyncDepth = 0;
// Options getters can read unrelated descriptors during a path-based read.
function nativePathRead() {
  if (!pathReadSyncDepth) return false;
  const formatter = Object.getOwnPropertyDescriptor(NativeError, 'prepareStackTrace');
  const limit = Object.getOwnPropertyDescriptor(NativeError, 'stackTraceLimit');
  if (formatter?.get || formatter?.set || formatter?.value || limit?.get || limit?.set) return false;
  try {
    const caller = new NativeError().stack.split('\n').slice(1).find(line => !line.includes(__filename + ':'));
    return /\bat (?:tryReadSync|Object\.readFileSync) \(node:fs:\d+:\d+\)$/.test(caller || '');
  } catch { return false; }
}
const originalReadFileSync = fs.readFileSync;
fs.readFileSync = function (...args) {
  dependency(args[0]);
  const pathRead = rawPath(args[0]) !== null;
  if (pathRead) pathReadSyncDepth++;
  try { return Reflect.apply(originalReadFileSync, this, args); }
  finally { if (pathRead) pathReadSyncDepth--; }
};
for (const name of ['read', 'readSync', 'readv', 'readvSync']) {
  wrapMethod(fs, name, () => {
    if (!busy && !(name === 'readSync' && nativePathRead())) unsafe('file-descriptor-input');
  });
}
for (const name of ['readFile', 'createReadStream']) wrapMethod(fs, name, dependency);
wrapMethod(fs.promises, 'readFile', dependency);
for (const object of [fs, fs.promises]) {
  for (const name of ['open', 'openSync']) wrapMethod(object, name, (file, flags = 'r') => {
    if (flags === 'r' || flags === 'rs' || flags === 0) dependency(file);
    else unsafe('file-write-or-unknown-open');
  });
  for (const name of ['readdir', 'readdirSync', 'opendir', 'opendirSync']) {
    wrapMethod(object, name, () => unsafe('directory-input'));
  }
  for (const name of ['stat', 'lstat', 'access', 'accessSync', 'exists']) {
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
fs.existsSync = function (file) {
  const value = nativeExists(file);
  if (busy) return value;
  const item = observedPath(file);
  if (item) observation('exists:value:' + item.name, value);
  else unsafe('external-metadata');
  return value;
};
for (const [kind, original] of [['stat', nativeStat], ['lstat', nativeLstat]]) {
  fs[kind + 'Sync'] = function (...args) {
    let stats;
    try { stats = Reflect.apply(original, fs, args); }
    catch (error) { if (!busy) unsafe('metadata-error'); throw error; }
    if (busy) return stats;
    const item = observedPath(args[0]);
    if (args.length !== 1 || !item || !stats) { unsafe('untracked-metadata'); return stats; }
    observation(kind + ':all:' + item.name, statValue(stats));
    return stats;
  };
}
for (const name of ['spawn', 'spawnSync', 'exec', 'execSync', 'execFile', 'execFileSync', 'fork']) {
  wrapMethod(cp, name, () => unsafe('subprocess'));
}
wrapMethod(require('node:net').Socket.prototype, 'connect', () => unsafe('network'));
syncBuiltinESMExports();
const environment = process.env;
function observeEnvironment(key) {
  if (!busy && !globalUnsafe.size && typeof key === 'string' &&
      runnerEnvironmentKey(key)) {
    const caller = new Error().stack?.split('\n')[3] || '';
    if (!caller.includes('(node:') && !caller.includes('at node:')) unsafe('volatile-environment');
  }
}
process.env = new Proxy(environment, {
  set(target, key, value) {
    target[key] = value;
    return true;
  },
  get(target, key) {
    observeEnvironment(key);
    return Reflect.get(target, key);
  },
  has(target, key) {
    observeEnvironment(key);
    return Reflect.has(target, key);
  },
  getOwnPropertyDescriptor(target, key) {
    observeEnvironment(key);
    return Reflect.getOwnPropertyDescriptor(target, key);
  },
  ownKeys(target) {
    observeEnvironment('INVOCATION_ID');
    return Reflect.ownKeys(target);
  },
});

function valid(record) {
  return record && record.passed === true && Array.isArray(record.unsafe) && !record.unsafe.length &&
    record.inputs && typeof record.inputs === 'object' &&
    Object.entries(record.inputs).every(([name, value]) =>
      typeof value === 'string' && value.length === 64 &&
      (tracked.has(name) ? fileHash(path.join(root, name)) : observationHash(name)) === value);
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
  write(destination, JSON.stringify({ context, tests, context_parts: contextParts }));
  write(destination + '.report.json', JSON.stringify(report, null, 2));
});

module.exports = { dependency, unsafe, wrapTest, hook };
