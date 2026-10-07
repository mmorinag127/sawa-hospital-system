// Source-first definitions only; execution and actual React verification pending.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const cp = require('node:child_process');
const ts = require('typescript');
const root = path.resolve(__dirname, '../..');
const file = 'frontend/src/pages/orders/[id].tsx';
const base = 'cfedb6ea72735bd6a2b5307699e5a9c35eb90503';
const before = cp.execFileSync('git', ['show', `${base}:${file}`], { cwd: root, encoding: 'utf8' });
const after = fs.readFileSync(path.join(root, file), 'utf8');
const addition = '                                  {effectiveHakodateJobStatus?.job_status ? <JobStatus job={effectiveHakodateJobStatus.job_status} showDetails /> : <span>ジョブ記録未取得</span>}\n';

test('page has only approved recorded-status integration edits', () => {
  let restored = after.replace('import { JobStatus, type JobStatusRecord } from "@sawa/ui";\n', '')
    .replace('  job_status?: JobStatusRecord | null;\n', '')
    .replace('  const effectiveHakodateJobStatus = hakodateJobStatus;\n',
      '  const effectiveHakodateJobStatus = hakodateJobStatus || order?.workflow_state?.reparse_state || null;\n')
    .replace('  const hakodateJobStage = firstNonEmptyText(effectiveHakodateJobStatus?.processing_stage);\n',
      '  const hakodateJobStage = firstNonEmptyText(\n    effectiveHakodateJobStatus?.processing_stage,\n    order?.ocr_processing_stage,\n  );\n')
    .replace(addition, '');
  assert.equal(restored, before);
  assert.equal(after.split(addition).length - 1, 1);
});

test('parser retains additive projection and display never borrows workflow status', () => {
  assert.match(after, /isObjectRecord\(value\) \? \(value as ReparseStatePayload\) : null/);
  assert.match(after, /setHakodateJobStatus\(asReparseStatePayload\(payload.job_status\)\)/);
  assert.match(after, /const effectiveHakodateJobStatus = hakodateJobStatus;/);
  assert.ok(!after.includes('hakodateJobStatus || order?.workflow_state?.reparse_state'));
  assert.ok(after.includes('const hakodateJobStage = firstNonEmptyText(effectiveHakodateJobStatus?.processing_stage);'));
  assert.ok(after.includes('<span>{hakodateJobStatusLabel}</span>'));
  assert.ok(after.includes('{hakodateJobDetail ? <span className="subtle">{hakodateJobDetail}</span> : null}'));
});

test('job stage has exactly the received recorded argument, never current order stage', () => {
  const source = ts.createSourceFile(file, after, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const stages = [];
  function visit(node) {
    if (ts.isVariableDeclaration(node) && node.name.getText(source) === 'hakodateJobStage') stages.push(node);
    ts.forEachChild(node, visit);
  }
  visit(source);
  assert.equal(stages.length, 1);
  const call = stages[0].initializer;
  assert.ok(ts.isCallExpression(call));
  assert.equal(call.expression.getText(source), 'firstNonEmptyText');
  assert.deepEqual(call.arguments.map(arg => arg.getText(source)), ['effectiveHakodateJobStatus?.processing_stage']);
});

test('actual JSX requires received projection, forwards record unchanged, keeps detail branch', () => {
  const source = ts.createSourceFile(file, after, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  assert.equal(source.parseDiagnostics.length, 0);
  const calls = [];
  function visit(node) {
    if (ts.isJsxSelfClosingElement(node) && node.tagName.getText(source) === 'JobStatus') calls.push(node);
    ts.forEachChild(node, visit);
  }
  visit(source);
  assert.equal(calls.length, 1);
  assert.deepEqual(calls[0].attributes.properties.map(prop => prop.name.getText(source)), ['job', 'showDetails']);
  assert.equal(calls[0].attributes.properties[0].initializer.expression.getText(source), 'effectiveHakodateJobStatus.job_status');
  const conditional = calls[0].parent;
  assert.ok(ts.isConditionalExpression(conditional));
  assert.equal(conditional.condition.getText(source), 'effectiveHakodateJobStatus?.job_status');
  assert.equal(conditional.whenFalse.getText(source), '<span>ジョブ記録未取得</span>');
});
