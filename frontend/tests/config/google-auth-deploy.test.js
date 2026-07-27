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
  assert.match(workflow, /ALLOW_BASIC_ONLY_AUTH: "0"/);
});

test("frontend and deploy script do not silently fall back to production Google OAuth", () => {
  const dockerfile = read("frontend/Dockerfile");
  const deployScript = read("scripts/deploy_web_with_checks.sh");

  assert.match(dockerfile, /^ARG NEXT_PUBLIC_GOOGLE_CLIENT_ID=$/m);
  assert.doesNotMatch(deployScript, /DEFAULT_GOOGLE_CLIENT_ID/);
  assert.match(
    deployScript,
    /GOOGLE_CLIENT_ID is required when Basic-only authentication is disabled/,
  );
});
