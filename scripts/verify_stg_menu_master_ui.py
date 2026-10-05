#!/usr/bin/env python3
"""Existing staging Actions only. Own-record cleanup is fail-closed, including on failure."""

import base64
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
import urllib.parse
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.engine import URL

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "backend"))

from run_stg_menu_master_migration import cloud_sql_proxy  # noqa: E402
from staging_db_target import (  # noqa: E402
    StagingConfigBlocked, load_service_db_config as _load_service_db_config,
    read_http, image_config, REGISTRY,
)
from stg_menu_master_safety import Blocked, SA_EMAIL, cleanup_owned, fingerprint, preflight, require  # noqa: E402

WEB = "https://web-stg-avlnzjjrca-dt.a.run.app"
WORKER = "https://worker-stg-avlnzjjrca-dt.a.run.app"
INSTANCE = "sawahospitalsystem:asia-northeast2:orders-stg"
OUTPUT = ROOT / "tmp/menu-master-live"


def require_context(env, head):
    fixed = {"GITHUB_ACTIONS": "true", "GITHUB_EVENT_NAME": "workflow_dispatch", "C1_LIVE_VERIFY": "true",
             "GITHUB_REF": "refs/heads/develop", "GITHUB_REF_NAME": "develop",
             "PROJECT_ID": "sawahospitalsystem", "REGION": "asia-northeast2", "WEB_SERVICE": "web-stg",
             "WORKER_SERVICE": "worker-stg", "WEB_URL": WEB, "WORKER_URL": WORKER, "LIVE_SERVICE_ACCOUNT": SA_EMAIL}
    for key, value in fixed.items():
        require(env.get(key) == value, "context-invalid-" + key)
    for key in ("GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT"):
        require(bool(re.fullmatch(r"[1-9][0-9]*", env.get(key, ""))), "context-invalid-" + key)
    require(bool(re.fullmatch(r"[0-9a-f]{40}", env.get("GITHUB_SHA", ""))) and env["GITHUB_SHA"] == head, "checkout-source-SHA-mismatch")
    require(bool(re.fullmatch(r"[0-9]+-[a-z0-9]+\.apps\.googleusercontent\.com", env.get("GOOGLE_OAUTH_CLIENT_ID", ""))), "staging-audience-missing")
    require(bool(env.get("PROD_GOOGLE_OAUTH_CLIENT_ID")), "production-audience-comparison-missing")
    require(env["GOOGLE_OAUTH_CLIENT_ID"] != env.get("PROD_GOOGLE_OAUTH_CLIENT_ID"), "production-audience-forbidden")


def private_command(args):
    result = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, timeout=60)
    require(result.returncode == 0, "cloud-command-failed-check-existing-Actions-identity")
    return result.stdout.strip()


def cloud(*args):
    return json.loads(private_command(["gcloud", *args, "--format=json"]))


def api(path, token, expected=200):
    require(path.startswith("/api/") and not path.startswith("//"), "api-path-outside-web")
    status, body = read_http(WEB + path, token)
    require(status == expected, "api-status-" + str(status) + "-expected-" + str(expected) + "-" + path.split("?")[0])
    return json.loads(body) if expected == 200 else None


def check_token(token, audience):
    try:
        encoded = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
        valid = claims.get("email") == SA_EMAIL and claims.get("email_verified") is True and claims.get("aud") == audience and claims.get("exp", 0) > time.time() + 120
    except Exception:
        valid = False
    require(valid, "fresh-deploy-SA-ID-token-required")
    # This only checks the intended principal; real API requests below perform signature validation.


def image_source(image_digest, component, sha, access_token):
    require(image_config(image_digest, component, access_token).get("Labels", {}).get("sawa.git_sha") == sha,
            "deployed-image-full-SHA-mismatch")


def deployed_sources(sha):
    access_token = private_command(["gcloud", "auth", "print-access-token"])
    sources = {}
    for service, component, url in (("web-stg", "frontend", WEB), ("worker-stg", "backend", WORKER)):
        data = cloud("run", "services", "describe", service, "--project=sawahospitalsystem", "--region=asia-northeast2")
        require(data["status"]["url"] == url, "service-URL-mismatch")
        traffic = [t for t in data["status"].get("traffic", []) if t.get("percent", 0) > 0]
        require(len(traffic) == 1 and traffic[0]["percent"] == 100, "split-or-unknown-traffic")
        revision = traffic[0].get("revisionName", "")
        require(revision.startswith(service + "-"), "serving-revision-missing")
        rev = cloud("run", "revisions", "describe", revision, "--project=sawahospitalsystem", "--region=asia-northeast2")
        require(any(c.get("type") == "Ready" and c.get("status") == "True" for c in rev["status"].get("conditions", [])), "serving-revision-not-ready")
        digest = rev["status"].get("imageDigest", "")
        image_source(digest, component, sha, access_token)
        sources[service] = {"revision": revision, "imageDigest": digest, "sourceSHA": sha}
    return sources


def write_json(path, data):
    temporary = path.with_suffix(".part")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False))
    temporary.replace(path)


def browser(ledger_path, token):
    env = {k: v for k, v in os.environ.items() if k not in ("DEBUG", "PWDEBUG", "NODE_OPTIONS")}
    env["LIVE_ID_TOKEN"] = token
    process = subprocess.Popen(["node", str(ROOT / "frontend/scripts/verify-menu-master-live.mjs"),
                                str(ledger_path), str(OUTPUT)], env=env, cwd=ROOT,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        try:
            status = process.wait(timeout=240)
        except subprocess.TimeoutExpired:
            raise Blocked("browser-deadline-exceeded-ledger-preserved") from None
        require(status == 0, "browser-failed-see-sanitized-browser-result")
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()


def run(result):
    result["phase"] = "context"
    sha = private_command(["git", "rev-parse", "HEAD"])
    require_context(os.environ, sha)
    require(not private_command(["git", "diff", "--name-only", "HEAD"]), "tracked-source-dirty")
    token = os.getenv("LIVE_ID_TOKEN", "")
    check_token(token, os.environ["GOOGLE_OAUTH_CLIENT_ID"])
    result["sourceSHA"] = sha
    result["phase"] = "immutable-image-source"
    result["sources"] = deployed_sources(sha)
    result["phase"] = "existing-cloud-database-config"
    web = _load_service_db_config("sawahospitalsystem", "asia-northeast2", "web-stg", expected_revision=result["sources"]["web-stg"]["revision"])
    worker = _load_service_db_config("sawahospitalsystem", "asia-northeast2", "worker-stg", expected_revision=result["sources"]["worker-stg"]["revision"])
    require((web.instance_connection_name, web.db_name, web.db_user) == (INSTANCE, "orders", "orders_app")
            and (worker.instance_connection_name, worker.db_name, worker.db_user) == (INSTANCE, "orders", "orders_app"), "cloud-database-target-mismatch")
    name = f"c1-live-{os.environ['GITHUB_RUN_ID']}-{os.environ['GITHUB_RUN_ATTEMPT']}-{uuid4().hex}"
    result["phase"] = "existing-SQL-proxy"
    with cloud_sql_proxy(INSTANCE):
        engine = create_engine(URL.create("postgresql+psycopg2", username=web.db_user, password=web.db_password,
                                          host="127.0.0.1", port=5432, database="orders"), hide_parameters=True)
        try:
            result["phase"] = "read-only-database-preflight"
            proof = preflight(engine, name)
            result["database"] = proof
            result["phase"] = "real-auth-and-filtered-API-preflight"
            identity = api("/api/auth/me", token)
            require(identity.get("auth_disabled") is False and identity.get("role") in ("operator", "admin"), "real-auth-me-rejected")
            portal = api("/api/portal/auth/me?system=hospital", token)
            require(portal.get("account") == SA_EMAIL and portal.get("role") == identity["role"] and "hospital" in portal.get("systems", []), "real-hospital-grant-rejected")
            query = "/api/menu-masters?q=" + name + "&offset=0&limit=50"
            listing = api(query, token)
            require(listing.get("items") == [] and listing.get("total") == 0, "filtered-name-not-absent")
            api("/api/menu-masters?q=" + name, None, 401)
            initial = {"version": 1, "source": sha, "runId": os.environ["GITHUB_RUN_ID"], "attempt": os.environ["GITHUB_RUN_ATTEMPT"],
                       "nonce": uuid4().hex, "name": name, "schemaSHA256": fingerprint(proof), "absentBefore": True,
                       "writeAttempted": False, "pending": None, "receipts": []}
            ledger_path = OUTPUT / "ledger.json"
            require(not ledger_path.exists(), "existing-ledger-refuse-reuse")
            write_json(ledger_path, initial)
            result["preflight"] = "passed"
            result["phase"] = "browser"
            result["browser"] = "failed"
            try:
                browser(ledger_path, token)
                result["browser"] = "passed"
            finally:
                result["phase"] = "owned-record-cleanup"
                try:
                    ledger = json.loads(ledger_path.read_text())
                    result["cleanup"] = cleanup_owned(engine, initial, ledger, proof)
                    for item_id in result["cleanup"]["deleted"]:
                        api("/api/menu-masters/" + urllib.parse.quote(item_id, safe=""), token, 404)
                    listing = api(query, token)
                    require(listing.get("items") == [] and listing.get("total") == 0, "cleanup-filtered-absence-failed")
                    result["cleanup"]["apiAbsenceVerified"] = True
                    result["phase"] = "finished" if result["browser"] == "passed" else "browser-failed-cleanup-verified"
                except Blocked as error:
                    result["cleanup"] = {"status": "unresolved", "code": str(error)}
                    raise
                except Exception:
                    result["cleanup"] = {"status": "unresolved", "code": "cleanup-transport-or-database-error-details-withheld"}
                    raise
        finally:
            engine.dispose()


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    result = {"scope": "actual stg UI/API with service-principal token; not human GIS login", "status": "failed"}
    try:
        run(result)
        result["status"] = "passed"
    except (Blocked, StagingConfigBlocked) as error:
        result["code"] = str(error)
    except Exception:
        result["code"] = "verification-transport-or-database-error-details-withheld"
    result["ownedProcessesStopped"] = True
    write_json(OUTPUT / "result.json", result)
    files = [p for p in OUTPUT.iterdir() if p.is_file() and p.name != "manifest.json"]
    write_json(OUTPUT / "manifest.json", {"sourceSHA": result.get("sourceSHA"),
               "artifacts": [{"file": p.name, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in files]})
    print("Menu-master live verification: " + result["status"] + "; see sanitized tmp/menu-master-live/result.json")
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
