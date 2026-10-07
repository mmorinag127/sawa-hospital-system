# H2 Harness Realm Repair

- Base: cd14d8bd36fbddd63fb70cbb1c4088be544cd6b1; branch codex/modernization-v1-hospital-h2-harness-20261008.
- Failure class: extraction harness creates returned payload literals in a new VM realm. Shared decision: actual(name, context). Invariant: evaluate unchanged production initializer/transpilation against explicit captured context, with current-realm literals and no implicit transport.
- Baseline named normal-columns/copy case fails (exit 1). diagnostic.log and literal-realm-proof.log independently show identical own keys and columns=[{quantity:0,unknown:null}], current-realm array/row, but foreign outer Object prototype. Both constructors are named Object; constructor identity is false. No values differ and no JSON stripping/property copying is used to pass assertions.
- actual() now uses default-current-context vm.compileFunction and snapshots the same spread context values into parameters. Primitive rebinding stays captured; original object references remain live. process/fetch/require/global/module/Buffer/globalThis are undefined unless explicitly present in context; no default transport.
- New focused contracts prove current Object/Array prototypes, zero/null, captured primitives/refs, missing required binding refusal, undefined ambient bindings and explicit-binding retention. Entire original file outside actual(), actual() transpilation/source extraction, all fixtures/assertions and PDF evaluator remain byte-identical. Production workflow-v2.tsx is byte-identical to base.

## Commands / Evidence

Own frontend cwd; read-only hospital-main node_modules copied into own node_modules (not npm-ci proof). No network/DB/provider/browser/server launches.

```sh
env -i PATH=/usr/bin:/bin /Users/mmorinag/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node --test --test-name-pattern='normal columns/copy retain' tests/h2-quad-owner.test.cjs > ../tmp/h2/baseline.log 2>&1
env -i PATH=/usr/bin:/bin HOME=/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-v1-h2-harness/tmp/h2/home TMPDIR=/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-v1-h2-harness/tmp/h2/tmp /Users/mmorinag/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node --test tests/h2-quad-owner.test.cjs tests/h2-harness-contract.test.cjs tests/facility-override-shape.test.cjs > ../tmp/h2/frozen.log 2>&1
```

- Final: 306 passed, 0 failed/skipped/cancelled, exit 0 (complete h2 file + shape checks + three new contracts).
- Retained history: final.log 304 pass/1 fail, caused by the new diagnostic test incorrectly reading owner.orderId rather than actual owner.context.orderId; original h2 case already passed. coherent.log 305 pass/0 fail before the parent-requested ambient-shadow contract was added. No original assertion or expected value changed.
- bindings-audit.log records inspected initializer identifiers and zero references to Node-only/network ambient names. Standard intrinsics retain current-context behavior; browser window is not supplied implicitly. This audit is bounded to recorded evaluated producer selections, not a global production claim.
- Source/patch hashes: tmp/h2/source-sha256.txt; proof/log hashes: tmp/h2/evidence-sha256.txt; exact frozen diff: tmp/h2/staged.patch.
- All owned processes finished. No commit/integration/deploy; parent staged-diff monitoring and fresh integrated-SHA verification remain pending.
