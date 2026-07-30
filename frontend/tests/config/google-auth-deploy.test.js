const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const repoRoot = path.resolve(__dirname, "../../..");
const read = (relativePath) => fs.readFileSync(path.join(repoRoot, relativePath), "utf8");

test("staging deploy requires a dedicated Google OAuth client", () => {
  const workflow = read(".github/workflows/deploy-stg.yml");

  assert.match(workflow, /GOOGLE_OAUTH_CLIENT_ID: \$\{\{ vars\.STG_GOOGLE_OAUTH_CLIENT_ID \}\}/);
  assert.match(workflow, /staging must not reuse the production Google OAuth client ID/);
  assert.match(workflow, /NEXT_PUBLIC_GOOGLE_CLIENT_ID=\$\{GOOGLE_OAUTH_CLIENT_ID\}/);
  assert.match(workflow, /GOOGLE_OAUTH_CLIENT_ID=\$\{GOOGLE_OAUTH_CLIENT_ID\}/);
  assert.doesNotMatch(workflow, /ALLOW_BASIC_ONLY_AUTH/);
});

test("frontend and deploy script do not silently fall back to production Google OAuth", () => {
  const dockerfile = read("frontend/Dockerfile");
  const deployScript = read("scripts/deploy_web_with_checks.sh");

  assert.match(dockerfile, /^ARG NEXT_PUBLIC_GOOGLE_CLIENT_ID=$/m);
  assert.doesNotMatch(deployScript, /DEFAULT_GOOGLE_CLIENT_ID/);
  assert.match(
    deployScript,
    /GOOGLE_CLIENT_ID is required/,
  );
});

test("login and API client expose no Basic authentication path", () => {
  const login = read("frontend/src/pages/login.tsx");
  const apiClient = read("frontend/src/services/apiClient.ts");

  assert.doesNotMatch(login, /Basic認証|Username|Password|encodeBasic/);
  assert.doesNotMatch(apiClient, /setBasicAuth|`Basic /);
});

test("deploy verification uses ephemeral Google OIDC and keeps positive safety gates", () => {
  const workerDeploy = read("scripts/deploy_worker_prod_with_checks.sh");
  const webDeploy = read("scripts/deploy_web_with_checks.sh");
  const predeploy = read("scripts/predeploy_env_checks.sh");

  for (const script of [workerDeploy, webDeploy]) {
    assert.match(script, /gcloud auth print-identity-token[\s\S]*--impersonate-service-account=[\s\S]*--include-email[\s\S]*--audiences=/);
    assert.match(script, /Authorization: Bearer \$\{DEPLOY_ID_TOKEN\}/);
    assert.doesNotMatch(script, /curl -sS -u|OPERATOR_PASSWORD/);
  }
  assert.match(workerDeploy, /check_ocr_sheet_quality_gate\.py/);
  assert.match(workerDeploy, /check_worker_surface_parity\.py/);
  assert.match(workerDeploy, /check_worker_web_surface_consistency\.py/);
  assert.match(workerDeploy, /portal\/auth\/me\?system=hospital/);
  assert.match(workerDeploy, /CI verification identity must be registered, active, and granted hospital access/);
  assert.match(predeploy, /check_predeploy_system_status\.py/);
  assert.match(predeploy, /worker_orders_unauthenticated/);
});

test("new users receive no automatic system grants", () => {
  const usersPage = read("frontend/src/pages/admin/users.tsx");
  assert.match(usersPage, /const emptyUser = \(\): User => \([\s\S]*systems: \[\]/);
  assert.doesNotMatch(usersPage, /systems: \["hospital", "shift", "school-lunch"\]/);
});

test("Secret Manager migration defaults to retaining legacy access for phase one", () => {
  const module = read("infra/terraform/modules/cloudrun/main.tf");
  const stgExample = read("infra/terraform/envs/stg/terraform.tfvars.example");
  const prodExample = read("infra/terraform/envs/prod/terraform.tfvars.example");
  assert.match(module, /variable "retain_legacy_project_secret_accessor"[\s\S]*default\s*= true/);
  assert.match(module, /var\.retain_legacy_project_secret_accessor \?/);
  assert.match(stgExample, /retain_legacy_project_secret_accessor = true/);
  assert.match(prodExample, /retain_legacy_project_secret_accessor = true/);
});

test("login pages allow the Google popup to return credentials", () => {
  const nextConfig = read("frontend/next.config.js");

  assert.match(nextConfig, /key: "Cross-Origin-Opener-Policy"/);
  assert.match(nextConfig, /value: "same-origin-allow-popups"/);
});
