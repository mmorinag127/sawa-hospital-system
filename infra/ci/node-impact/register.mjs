import Module, { registerHooks, createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import tracker from './core.cjs';

const wrapper = new URL('./test.cjs', import.meta.url).href;
const facade = createRequire(import.meta.url)('./test.cjs');
const load = Module._load;
Module._load = function (specifier, parent, isMain) {
  if (specifier === 'node:test') return facade;
  return Reflect.apply(load, this, [specifier, parent, isMain]);
};
registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier === 'node:test' && context.parentURL !== wrapper) {
      return { url: wrapper, shortCircuit: true };
    }
    const result = nextResolve(specifier, context);
    if (result.url.startsWith('file:')) tracker.dependency(fileURLToPath(result.url));
    return result;
  },
});
