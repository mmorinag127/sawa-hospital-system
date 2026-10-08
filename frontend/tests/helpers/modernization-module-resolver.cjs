// Use this repository's installed packages, never a sibling worktree runtime.
const { createRequire } = require('node:module');
const { pathToFileURL } = require('node:url');
const path = require('node:path');
const fs = require('node:fs');

function importFile(frontend, name, requirePackage) {
  const parts = name.split('/');
  const packageName = name.startsWith('@') ? parts.slice(0, 2).join('/') : parts[0];
  const subpath = parts.slice(name.startsWith('@') ? 2 : 1).join('/');
  const root = path.join(frontend, 'node_modules', packageName);
  const metadata = JSON.parse(fs.readFileSync(path.join(root, 'package.json'), 'utf8'));
  let entry = metadata.exports?.[subpath ? `./${subpath}` : '.'];
  while (entry && typeof entry === 'object') entry = entry.import ?? entry.default;
  return typeof entry === 'string' ? path.resolve(root, entry) : requirePackage.resolve(name);
}

async function modules(frontend) {
  const requirePackage = createRequire(path.join(frontend, 'package.json'));
  const shared = new Map();
  for (const name of ['react', 'react/jsx-runtime', '@sawa/ui', 'react-hook-form',
    '@mui/material', '@mui/material/styles', '@tanstack/react-query']) {
    // ESM packages and shared-ui imports must have the same RHF/Query context.
    const resolved = importFile(frontend, name, requirePackage);
    const imported = await import(pathToFileURL(resolved).href);
    shared.set(name, imported.default && !imported.useForm && !imported.SawaProvider
      && !imported.QueryClient && !imported.Button ? imported.default : imported);
  }
  return { requirePackage, shared };
}

function navigation(React, route = '/orders/synthetic-order/workflow-v2') {
  const listeners = new Map();
  const router = {
    isReady: true, asPath: route, pathname: route, query: { id: 'synthetic-order' },
    events: {
      on(name, fn) { if (!listeners.has(name)) listeners.set(name, new Set()); listeners.get(name).add(fn); },
      off(name, fn) { listeners.get(name)?.delete(fn); },
      emit(name, ...args) { for (const fn of listeners.get(name) || []) fn(...args); },
    },
    async push(url) { router.events.emit('routeChangeStart', url); router.asPath = url; return true; },
    async replace(url) { return router.push(url); }, beforePopState() {},
  };
  function Link({ href, children, onNavigate, ...props }) {
    return React.createElement('a', { ...props, href: typeof href === 'string' ? href : href.pathname,
      onClick(event) { event.preventDefault(); onNavigate?.({ preventDefault: () => event.preventDefault() }); } }, children);
  }
  return { router, imports: new Map([
    ['next/router', { useRouter: () => router }],
    ['next/link', { __esModule: true, default: Link }],
  ]) };
}
module.exports = { modules, navigation };
