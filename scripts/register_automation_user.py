"""Register the approved shift-only identity using the existing admin API."""

import json
import os
import urllib.error
import urllib.request


def main():
    if os.environ.get("GITHUB_ACTIONS") != "true":
        raise SystemExit("Only GitHub Actions may register the automation identity")
    base = os.environ["WEB_URL"].rstrip("/")
    account = os.environ["AUTOMATION_AUTH_EMAIL"]
    environment = {"https://web-stg-avlnzjjrca-dt.a.run.app": "stg",
                   "https://web-prod-avlnzjjrca-dt.a.run.app": "prod"}.get(base)
    if not environment or account != f"sawa-ui-verify-{environment}@sawahospitalsystem.iam.gserviceaccount.com":
        raise SystemExit("Automation environment identity mismatch")
    token = os.environ["DEPLOY_ID_TOKEN"]

    def request(method, path, body=None):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(base + path, data=data, method=method, headers={
            "Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            raise SystemExit(f"Automation registration rejected: HTTP {exc.code}") from None

    expected = {"account": account, "role": "operator", "status": "active", "systems": ["shift"]}
    rows = request("GET", "/api/portal/users")["items"]
    matches = [row for row in rows if row["account"].lower() == account]
    if len(matches) > 1:
        raise SystemExit("Duplicate automation accounts; no changes made")
    if matches:
        actual = {key: matches[0][key] for key in expected}
        if actual != expected:
            raise SystemExit("Existing automation grants differ; refusing to overwrite")
    else:
        created = request("POST", "/api/portal/users", expected)["user"]
        if {key: created[key] for key in expected} != expected:
            raise SystemExit("Automation registration result mismatch")
    print(f"Verified shift-only automation operator registration: {environment}")


if __name__ == "__main__":
    main()
