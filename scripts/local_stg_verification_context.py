"""Explicit local staging verification identity, separate from Actions identity."""

import re


def require_local_source(env, head, require):
    require(not env.get("GITHUB_ACTIONS") and not env.get("CI"), "local-verification-required")
    source = env.get("EXPECTED_SOURCE_SHA", "")
    require(bool(re.fullmatch(r"[0-9a-f]{40}", source)) and source == head,
            "checkout-source-SHA-mismatch")
    require(bool(re.fullmatch(r"[0-9a-f]{32}", env.get("LOCAL_STG_RUN_ID", ""))),
            "local-run-id-required")
    require(int(env["LOCAL_STG_RUN_ID"], 16) > 0, "local-run-id-required")
    require(bool(env.get("PROD_GOOGLE_OAUTH_CLIENT_ID")),
            "production-audience-comparison-missing")
    audience = env.get("GOOGLE_OAUTH_CLIENT_ID", "")
    require(bool(re.fullmatch(r"[0-9]+-[a-z0-9]+\.apps\.googleusercontent\.com", audience)),
            "staging-audience-missing")
    require(audience != env["PROD_GOOGLE_OAUTH_CLIENT_ID"],
            "production-audience-forbidden")
