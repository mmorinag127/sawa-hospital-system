"""Exact release context shared by hospital Actions-only deployment helpers."""

import re

PROJECT = "sawahospitalsystem"
REGION = "asia-northeast2"


def require_context(env):
    expected = {"GITHUB_ACTIONS": "true", "GITHUB_EVENT_NAME": "workflow_dispatch",
                "GITHUB_REPOSITORY": "mmorinag127/sawa-hospital-system",
                "PROJECT_ID": PROJECT, "REGION": REGION}
    if any(env.get(key) != value for key, value in expected.items()):
        raise RuntimeError("owner-dispatched hospital Actions context required")
    owner = env.get("GITHUB_REPOSITORY_OWNER")
    if owner != "mmorinag127" or any(env.get(key) != owner for key in ("GITHUB_ACTOR", "GITHUB_TRIGGERING_ACTOR")):
        raise RuntimeError("repository owner execution required")
    if not re.fullmatch(r"[1-9][0-9]*", env.get("GITHUB_RUN_ID", "")):
        raise RuntimeError("Actions run id required")
    ref = env.get("GITHUB_REF_NAME", "")
    if env.get("GITHUB_REF") != "refs/heads/" + ref:
        raise RuntimeError("release ref mismatch")
    if ref == "develop":
        target = "stg"
    elif re.fullmatch(r"release/prod-[A-Za-z0-9._/-]+", ref):
        if env.get("PRODUCTION_CONFIRMATION") != "DEPLOY_PRODUCTION":
            raise RuntimeError("explicit production confirmation required")
        target = "prod"
    else:
        raise RuntimeError("develop or release/prod-* required")
    if env.get("CLOUDSDK_AUTH_IMPERSONATE_SERVICE_ACCOUNT"):
        raise RuntimeError("credential override forbidden")
    return target
