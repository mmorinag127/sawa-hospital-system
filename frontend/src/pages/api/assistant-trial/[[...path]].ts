import type { NextApiRequest, NextApiResponse } from "next";
import { portalProxyHeaders } from "../../../services/portalProxySession";

export const config = { api: { bodyParser: false, responseLimit: "25mb" } };

export default async function handler(req: NextApiRequest, res: NextApiResponse) {
  try {
  if (process.env.SAWA_ASSISTANT_TRIAL_ENABLED !== "true" || !process.env.SAWA_TRIAL_WEB_TOKEN) {
    res.status(404).json({ detail: "試用版は有効になっていません" }); return;
  }
  const incoming = new Headers();
  for (const [key, value] of Object.entries(req.headers)) {
    if (value !== undefined) incoming.set(key, Array.isArray(value) ? value.join(",") : value);
  }
  let token: string | null;
  try {
    token = portalProxyHeaders("/api/assistant-trial", req.method || "GET", incoming,
      req.cookies || {}, process.env.PORTAL_PUBLIC_ORIGIN || "").get("authorization");
  } catch {
    res.status(403).json({ detail: "認証リクエストを確認できません" }); return;
  }
  if (!token?.startsWith("Bearer ") || !process.env.API_PROXY_TARGET) {
    res.status(401).json({ detail: "SAWAへログインしてください" }); return;
  }
  const auth = await fetch(`${process.env.API_PROXY_TARGET.replace(/\/$/, "")}/portal/auth/me`, { headers: { Authorization: token }, cache: "no-store", redirect: "error", signal: AbortSignal.timeout(10000) });
  if (!auth.ok) { res.status(auth.status).json({ detail: "SAWAの利用権限を確認できません" }); return; }
  const identity = await auth.json();
  // The experimental service is explicitly a single authorized operator workspace.
  if (!process.env.SAWA_TRIAL_OPERATOR_ACCOUNT || identity.account !== process.env.SAWA_TRIAL_OPERATOR_ACCOUNT) {
    res.status(403).json({ detail: "試用担当者だけが利用できます" }); return;
  }
  const segments = req.query.path || [];
  if (!Array.isArray(segments) || segments.some(p => !/^[a-zA-Z0-9_-]+$/.test(p))) { res.status(400).end(); return; }
  const chunks: Buffer[] = []; let length = 0;
  for await (const chunk of req) { length += chunk.length; if (length > 21 * 1024 * 1024) { res.status(413).end(); return; } chunks.push(Buffer.from(chunk)); }
  const query = new URLSearchParams();
  for (const [key,value] of Object.entries(req.query)) {
    if (key === "path") continue;
    for (const item of Array.isArray(value) ? value : value === undefined ? [] : [value]) query.append(key,item);
  }
  const target = process.env.SAWA_ASSISTANT_TRIAL_URL || "http://127.0.0.1:8128";
  const endpoint = new URL(target);
  if (!["http:", "https:"].includes(endpoint.protocol) || endpoint.username || endpoint.password || (endpoint.protocol === "http:" && !["127.0.0.1", "localhost"].includes(endpoint.hostname))) {
    res.status(503).json({ detail: "試用サービスの接続先設定が不正です" }); return;
  }
  const isBusinessRequest = segments[0] === "runs" && (
    req.method === "POST" && (segments.length === 3 && ["reply","decision","resume","stop","rollback","refresh-usage"].includes(segments[2])
      || segments.length === 5 && segments[2] === "faxes" && segments[4] === "decision")
    || req.method === "GET" && segments.length === 4 && segments[2] === "artifacts"
  );
  const response = await fetch(`${target.replace(/\/$/, "")}/${segments.join("/")}?${query}`, {
    method: req.method, headers: { "X-Trial-Token": process.env.SAWA_TRIAL_WEB_TOKEN, Authorization: token, ...(req.headers["content-type"] ? { "Content-Type": req.headers["content-type"] } : {}) },
    body: chunks.length ? Buffer.concat(chunks) : undefined, signal: AbortSignal.timeout(isBusinessRequest ? 270000 : 60000), redirect: "error",
  });
  res.status(response.status);
  for (const key of ["content-type", "content-disposition", "x-content-sha256", "cache-control", "content-security-policy", "x-content-type-options"]) { const value = response.headers.get(key); if (value) res.setHeader(key, value); }
  const bytes = Buffer.from(await response.arrayBuffer());
  if (bytes.length > 25 * 1024 * 1024) { res.status(502).json({ detail: "出力サイズ上限を超えています" }); return; }
  res.send(bytes);
  } catch {
    if (!res.headersSent) res.status(503).json({ detail: "試用サービスへ接続できません。実行履歴で結果を確認してください。同じ依頼を自動再送しません。" });
  }
}
