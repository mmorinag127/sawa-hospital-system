const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const yaml = require('js-yaml');

const root = path.resolve(__dirname, '../../..');
const workflow = yaml.load(fs.readFileSync(path.join(root, '.github/workflows/deploy-stg.yml'), 'utf8'));
const optIn = "github.event_name == 'workflow_dispatch' && inputs.verify_menu_master_ui == true";

test('data-writing C1 verification is dispatch opt-in after successful web deploy, with fresh existing WIF', () => {
  assert.deepEqual(workflow.on.workflow_dispatch.inputs.verify_menu_master_ui, {
    description: "Create, verify and strictly clean up this run's own staging menu master",
    required: false, type: 'boolean', default: false,
  });
  const job = workflow.jobs['deploy-frontend'];
  assert.deepEqual(job.permissions, { contents: 'read', 'id-token': 'write' });
  const steps = job.steps, deploy = steps.findIndex(s => s.name === 'Deploy web-stg');
  const extra = steps.slice(deploy + 1);
  assert.equal(extra.length, 7);
  for (const step of extra.slice(0, -1)) assert.equal(step.if, optIn);
  assert.match(extra[3].run, /playwright install --with-deps webkit/);
  assert.match(extra[3].run, /uv sync --project backend --extra dev --frozen/);
  assert.equal(extra[4].id, 'auth-menu-master-live');
  assert.equal(extra[4].uses, 'google-github-actions/auth@v3');
  assert.deepEqual(extra[4].with, {
    project_id: '${{ env.PROJECT_ID }}',
    workload_identity_provider: '${{ secrets.GCP_WORKLOAD_IDENTITY_PROVIDER_STG }}',
    service_account: '${{ secrets.GCP_DEPLOY_SERVICE_ACCOUNT_STG }}',
    token_format: 'id_token', id_token_audience: '${{ env.GOOGLE_OAUTH_CLIENT_ID }}', id_token_include_email: true,
  });
  assert.deepEqual(extra[5].env, { C1_LIVE_VERIFY: 'true',
    LIVE_SERVICE_ACCOUNT: '${{ secrets.GCP_DEPLOY_SERVICE_ACCOUNT_STG }}',
    LIVE_ID_TOKEN: '${{ steps.auth-menu-master-live.outputs.id_token }}',
  });
  assert.equal(extra[5].run, 'uv run --project backend --extra dev --frozen python scripts/verify_stg_menu_master_ui.py');
  assert.equal(extra[6].if, 'always() && ' + optIn);
  assert.equal(extra[6].with.path, 'tmp/menu-master-live/');
  assert.equal(extra[6].with['if-no-files-found'], 'error');
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
