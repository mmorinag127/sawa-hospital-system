#!/usr/bin/env bash
set -euo pipefail

# Exception-only path for planned downtime. This replaces web-prod with a static maintenance page.

PROJECT_ID="${PROJECT_ID:-sawahospitalsystem}"
REGION="${REGION:-asia-northeast2}"
SERVICE="${SERVICE:-web-prod}"
"$(cd "$(dirname "$0")" && pwd)/require_ci_cd_deploy.sh" "${SERVICE}"
REPOSITORY="${REPOSITORY:-backend}"
IMAGE_TAG="${IMAGE_TAG:-prod-maintenance-$(date -u +%Y%m%dT%H%M%SZ)}"
IMAGE="${IMAGE:-${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPOSITORY}/frontend:${IMAGE_TAG}}"
OUT_DIR="${OUT_DIR:-tmp/prod_maintenance_page}"
CONFIRM="${CONFIRM:-}"

if [[ "$CONFIRM" != "DEPLOY_PROD_MAINTENANCE" ]]; then
  echo "Refusing to deploy maintenance page without CONFIRM=DEPLOY_PROD_MAINTENANCE" >&2
  exit 2
fi

rm -rf "$OUT_DIR"
mkdir -p "$OUT_DIR"

cat >"$OUT_DIR/package.json" <<'EOF'
{"scripts":{"start":"node server.js"},"dependencies":{}}
EOF

cat >"$OUT_DIR/server.js" <<'EOF'
const http = require("http");
const port = process.env.PORT || 8080;
const html = `<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>アップデート中</title>
  <style>
    :root { color-scheme: light; font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
    body { margin: 0; min-height: 100vh; display: grid; place-items: center; background: #f7f8fa; color: #17202a; }
    main { width: min(680px, calc(100vw - 40px)); padding: 40px 0; }
    h1 { margin: 0 0 16px; font-size: clamp(28px, 5vw, 40px); line-height: 1.2; letter-spacing: 0; }
    p { margin: 0 0 10px; font-size: 17px; line-height: 1.75; }
    .meta { margin-top: 28px; color: #5b6573; font-size: 14px; }
  </style>
</head>
<body>
  <main>
    <h1>現在アップデート中です</h1>
    <p>システム更新のため、一時的に画面を停止しています。</p>
    <p>更新が完了次第、通常画面に戻ります。</p>
    <p class="meta">Sawa Hospital Order System</p>
  </main>
</body>
</html>`;

http.createServer((req, res) => {
  res.writeHead(200, {
    "Content-Type": "text/html; charset=utf-8",
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"
  });
  res.end(html);
}).listen(port, "0.0.0.0");
EOF

cat >"$OUT_DIR/Dockerfile" <<'EOF'
FROM node:20-slim
WORKDIR /app
ENV NODE_ENV=production
ENV PORT=8080
COPY package.json server.js ./
CMD ["npm", "run", "start"]
EOF

gcloud builds submit "$OUT_DIR" --project="$PROJECT_ID" --tag "$IMAGE"
gcloud run deploy "$SERVICE" --project="$PROJECT_ID" --region="$REGION" --image="$IMAGE" --quiet
gcloud run services describe "$SERVICE" --project="$PROJECT_ID" --region="$REGION" --format='yaml(status.url,status.traffic,status.latestReadyRevisionName,spec.template.spec.containers[].image)'

printf '%s\n' "$IMAGE"
