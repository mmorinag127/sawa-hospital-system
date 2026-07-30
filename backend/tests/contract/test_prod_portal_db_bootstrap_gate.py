from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = ROOT.parent


def test_prod_workflow_requires_bootstrap_gate_before_backend_deploy():
    workflow = (REPO_ROOT / ".github" / "workflows" / "deploy-prod.yml").read_text(
        encoding="utf-8"
    )

    assert "prod-db-bootstrap-gate:" in workflow
    assert "environment: production" in workflow
    assert "PORTAL_BOOTSTRAP_ADMIN_EMAIL: ${{ vars.PROD_PORTAL_BOOTSTRAP_ADMIN_EMAIL }}" in workflow
    assert 'CLOUD_SQL_PROXY_VERSION: "2.24.1"' in workflow
    assert "CLOUD_SQL_PROXY_SHA256" in workflow
    assert "sha256sum -c -" in workflow
    assert "scripts/portal_prod_db_bootstrap.py" in workflow
    assert "- prod-db-bootstrap-gate" in workflow

    approval_pos = workflow.index("production-approval:")
    gate_pos = workflow.index("prod-db-bootstrap-gate:")
    deploy_pos = workflow.index("deploy-backend:")
    assert approval_pos < gate_pos < deploy_pos


def test_prod_bootstrap_script_is_ci_only_and_release_branch_only():
    source = (ROOT / "scripts" / "portal_prod_db_bootstrap.py").read_text(encoding="utf-8")

    assert 'os.getenv("GITHUB_ACTIONS") != "true"' in source
    assert 'release/prod-*' in source
    assert "PORTAL_BOOTSTRAP_ADMIN_EMAIL" in source
    assert "run_portal_access_bootstrap_gate" in source
