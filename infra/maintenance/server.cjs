const http = require('node:http');

const html = `<!doctype html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>アップデート中 | SAWA</title>
<style>
body{margin:0;min-height:100vh;display:grid;place-items:center;background:#f7f8fa;color:#17202a;font-family:system-ui,sans-serif}
main{width:min(680px,calc(100% - 40px));padding:40px 0}h1{font-size:28px;line-height:1.4;overflow-wrap:anywhere}p{font-size:17px;line-height:1.75}
</style></head><body><main><h1>現在アップデート中です</h1>
<p>システム更新のため、一時的にご利用を停止しています。</p>
<p>更新が完了次第、通常画面に戻ります。</p><p>SAWA</p></main></body></html>`;

function handler(req, res) {
  const health = req.method === 'GET' && req.url === '/healthz';
  const api = req.url.startsWith('/api/') || req.method !== 'GET';
  res.writeHead(health ? 200 : 503, {
    'Content-Type': health || api ? 'application/json; charset=utf-8' : 'text/html; charset=utf-8',
    'Cache-Control': 'no-store',
    'Retry-After': '300',
    'X-Content-Type-Options': 'nosniff',
    'X-Sawa-Maintenance': '1',
  });
  res.end(health ? '{"maintenance":true}' : api ? '{"error":"maintenance"}' : html);
}

if (require.main === module) {
  http.createServer(handler).listen(Number(process.env.PORT || 8080), '0.0.0.0');
}
module.exports = { handler };
