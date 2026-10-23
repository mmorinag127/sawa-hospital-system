const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const yaml = require('js-yaml');

const root = path.resolve(__dirname, '../../..');
const workflow = yaml.load(fs.readFileSync(path.join(root, '.github/workflows/deploy-stg.yml'), 'utf8'));
const read = relative => fs.readFileSync(path.join(root, relative), 'utf8');

test('deployment excludes hosted menu/output browser checks and remains manual', () => {
  assert.deepEqual(Object.keys(workflow.on), ['workflow_dispatch']);
  const inputs = workflow.on.workflow_dispatch.inputs;
  for (const key of ['verify_menu_master_ui', 'verify_output_source_order_id', 'verify_output_source_date']) {
    assert.equal(inputs[key], undefined);
  }
  const steps = Object.values(workflow.jobs).flatMap(job => job.steps || []);
  for (const step of steps) {
    assert.doesNotMatch(step.run || '', /verify_stg_menu_master_ui|verify_stg_output_source|playwright install/);
    assert.ok(!['auth-menu-master-live', 'auth-output-source-live'].includes(step.id));
  }
});

test('local live checks keep exact source, staging identity and owned cleanup', () => {
  const context = read('scripts/local_stg_verification_context.py');
  assert.match(context, /not env.get\("GITHUB_ACTIONS"\) and not env.get\("CI"\)/);
  for (const key of ['EXPECTED_SOURCE_SHA', 'LOCAL_STG_RUN_ID', 'GOOGLE_OAUTH_CLIENT_ID', 'PROD_GOOGLE_OAUTH_CLIENT_ID']) {
    assert.ok(context.includes(key));
  }
  assert.match(context, /source == head/);
  const menu = read('scripts/verify_stg_menu_master_ui.py');
  const output = read('scripts/verify_stg_output_source.py');
  for (const source of [menu, output]) {
    assert.ok(source.includes('require_local_source(env, head, require)'));
    assert.ok(source.includes('check_token('));
    assert.ok(source.includes('deployed_sources('));
    assert.doesNotMatch(source, /GITHUB_RUN_ID|GITHUB_RUN_ATTEMPT|GITHUB_SHA/);
  }
  assert.ok(menu.includes('cleanup_owned(engine, initial, ledger, proof)'));
  assert.ok(menu.includes('cleanup-filtered-absence-failed'));
  assert.ok(menu.includes('existing-ledger-refuse-reuse'));
  assert.ok(output.includes('OUTPUT_SOURCE_VERIFY_ORDER_ID'));
  assert.ok(output.includes('OUTPUT_SOURCE_VERIFY_DATE'));
  assert.ok(output.includes('GET-path-rejected'));
});

test('both staging image builds bind metadata to the full source SHA, not a tag guess', () => {
  for (const job of ['build-frontend', 'build-backend']) {
    const build = workflow.jobs[job].steps.find(s => s.id === 'build');
    assert.match(build.run, /--label "sawa\.git_sha=\$\{GITHUB_SHA\}"/);
  }
  const verify = fs.readFileSync(path.join(root, 'scripts/verify_stg_menu_master_ui.py'), 'utf8');
  const image = fs.readFileSync(path.join(root, 'scripts/staging_db_target.py'), 'utf8');
  assert.match(image, /hashlib.sha256\(raw\).hexdigest\(\) == digest/);
  assert.match(image, /hashlib.sha256\(raw\).hexdigest\(\) == config/);
  assert.match(verify, /get\("sawa.git_sha"\) == sha/);
});
