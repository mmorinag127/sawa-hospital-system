#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-sawahospitalsystem}"
REGION="${REGION:-asia-northeast2}"
SERVICE="${SERVICE:-worker-prod}"
WORKER_URL="${WORKER_URL:-https://worker-prod-avlnzjjrca-dt.a.run.app}"
OPERATOR_USER="${OPERATOR_USER:-}"
OPERATOR_PASSWORD="${OPERATOR_PASSWORD:-}"

if [[ -z "${OPERATOR_USER}" || -z "${OPERATOR_PASSWORD}" ]]; then
  echo "OPERATOR_USER / OPERATOR_PASSWORD are required"
  exit 1
fi

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "${TMP_DIR}"' EXIT

echo "[1/6] load secret versions"
for v in 1 2 3; do
  gcloud secrets versions access "${v}" --secret=gmail-client-id --project="${PROJECT_ID}" > "${TMP_DIR}/gmail_client_id_${v}" 2>/dev/null || true
done
for v in 1 2 3; do
  gcloud secrets versions access "${v}" --secret=gmail-client-secret --project="${PROJECT_ID}" > "${TMP_DIR}/gmail_client_secret_${v}" 2>/dev/null || true
done
for v in 1 2; do
  gcloud secrets versions access "${v}" --secret=web-client-1-secret --project="${PROJECT_ID}" > "${TMP_DIR}/web_client_secret_${v}" 2>/dev/null || true
done
for v in 1 2 3 4; do
  gcloud secrets versions access "${v}" --secret=gmail-refresh-token --project="${PROJECT_ID}" > "${TMP_DIR}/gmail_refresh_${v}" 2>/dev/null || true
done

echo "[2/6] probe oauth refresh token compatibility"
MATCH_FILE="${TMP_DIR}/match.json"
python3 - <<'PY' "${TMP_DIR}" "${MATCH_FILE}"
import itertools
import json
from pathlib import Path
import sys
import urllib.parse
import urllib.request

base = Path(sys.argv[1])
out_path = Path(sys.argv[2])

client_ids = []
for p in sorted(base.glob("gmail_client_id_*")):
    raw = p.read_text().strip()
    if not raw:
        continue
    version = p.name.split("_")[-1]
    for part in [x.strip() for x in raw.split(",") if x.strip()]:
        client_ids.append((version, part))

dedup = []
seen = set()
for version, client_id in client_ids:
    if client_id in seen:
        continue
    seen.add(client_id)
    dedup.append((version, client_id))
client_ids = dedup

client_secrets = []
for p in sorted(base.glob("gmail_client_secret_*")):
    value = p.read_text().strip()
    if value:
        client_secrets.append((f"gmail:{p.name.split('_')[-1]}", value))
for p in sorted(base.glob("web_client_secret_*")):
    value = p.read_text().strip()
    if value:
        client_secrets.append((f"web:{p.name.split('_')[-1]}", value))

refresh_tokens = []
for p in sorted(base.glob("gmail_refresh_*")):
    value = p.read_text().strip()
    if value:
        refresh_tokens.append((p.name.split("_")[-1], value))

match = None
for (cid_ver, cid), (sec_ver, sec), (ref_ver, ref) in itertools.product(client_ids, client_secrets, refresh_tokens):
    data = urllib.parse.urlencode(
        {
            "client_id": cid,
            "client_secret": sec,
            "refresh_token": ref,
            "grant_type": "refresh_token",
        }
    ).encode()
    req = urllib.request.Request("https://oauth2.googleapis.com/token", data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = resp.read().decode("utf-8", "ignore")
            payload = json.loads(body)
            if resp.status == 200 and payload.get("access_token"):
                match = {
                    "client_id_version": cid_ver,
                    "client_id": cid,
                    "client_secret_source": sec_ver,
                    "client_secret": sec,
                    "refresh_token_version": ref_ver,
                    "refresh_token": ref,
                }
                break
    except Exception:
        continue
if match:
    out_path.write_text(json.dumps(match), encoding="utf-8")
    print("found_match=1")
else:
    print("found_match=0")
PY

if [[ ! -f "${MATCH_FILE}" ]]; then
  echo "[FAIL] no valid oauth refresh combination found across known secret versions"
  echo "Action required: issue a new Gmail refresh token and add it to Secret Manager (gmail-refresh-token)."
  exit 2
fi

echo "[3/6] promote working secret values to latest"
python3 - <<'PY' "${MATCH_FILE}" "${TMP_DIR}/match_client_id" "${TMP_DIR}/match_client_secret" "${TMP_DIR}/match_refresh"
import json
import sys
from pathlib import Path
match = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
Path(sys.argv[2]).write_text(match["client_id"], encoding="utf-8")
Path(sys.argv[3]).write_text(match["client_secret"], encoding="utf-8")
Path(sys.argv[4]).write_text(match["refresh_token"], encoding="utf-8")
print(f"match client_id_version={match['client_id_version']} client_secret_source={match['client_secret_source']} refresh_version={match['refresh_token_version']}")
PY

gcloud secrets versions add gmail-client-id --project="${PROJECT_ID}" --data-file="${TMP_DIR}/match_client_id" >/dev/null
gcloud secrets versions add gmail-client-secret --project="${PROJECT_ID}" --data-file="${TMP_DIR}/match_client_secret" >/dev/null
gcloud secrets versions add gmail-refresh-token --project="${PROJECT_ID}" --data-file="${TMP_DIR}/match_refresh" >/dev/null

echo "[4/6] redeploy ${SERVICE} to load latest secrets"
IMAGE="$(gcloud run services describe "${SERVICE}" --project="${PROJECT_ID}" --region="${REGION}" --format='value(spec.template.spec.containers[0].image)')"
gcloud run deploy "${SERVICE}" \
  --project="${PROJECT_ID}" \
  --region="${REGION}" \
  --image="${IMAGE}" \
  --quiet >/dev/null

echo "[5/6] trigger /watch-refresh"
HTTP_CODE="$(
  curl -sS -u "${OPERATOR_USER}:${OPERATOR_PASSWORD}" \
    -o "${TMP_DIR}/watch_refresh.json" \
    -w "%{http_code}" \
    -X POST "${WORKER_URL}/watch-refresh"
)"
if [[ "${HTTP_CODE}" != "200" ]]; then
  echo "[FAIL] watch-refresh returned status=${HTTP_CODE}"
  cat "${TMP_DIR}/watch_refresh.json"
  exit 3
fi

echo "[6/6] verify system/status"
STATUS="$(
  curl -sS -u "${OPERATOR_USER}:${OPERATOR_PASSWORD}" "${WORKER_URL}/system/status" \
    | python3 -c 'import json,sys; s=json.load(sys.stdin); print((s.get("gmail_watch") or {}).get("status") or "")'
)"
if [[ "${STATUS}" != "ok" ]]; then
  echo "[FAIL] gmail_watch.status is ${STATUS}"
  exit 4
fi
echo "[OK] gmail watch recovered"
