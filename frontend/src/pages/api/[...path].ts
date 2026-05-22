import type { NextApiRequest, NextApiResponse } from "next";
import axios from "axios";
import { pipeline } from "stream/promises";

const getTarget = () =>
  process.env.API_PROXY_TARGET || "https://worker-prod-avlnzjjrca-dt.a.run.app";

export const config = {
  api: {
    bodyParser: false,
    responseLimit: false,
    externalResolver: true,
  },
};

const hopByHopHeaders = new Set([
  "connection",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailer",
  "transfer-encoding",
  "upgrade",
]);

const copyRequestHeaders = (req: NextApiRequest) => {
  const headers: Record<string, string> = {};
  for (const [key, value] of Object.entries(req.headers)) {
    const normalizedKey = key.toLowerCase();
    if (!value || normalizedKey === "host" || hopByHopHeaders.has(normalizedKey)) continue;
    headers[key] = Array.isArray(value) ? value.join(",") : value;
  }
  return headers;
};

const copyResponseHeaders = (res: NextApiResponse, headers: Record<string, unknown>) => {
  for (const [key, value] of Object.entries(headers)) {
    const normalizedKey = key.toLowerCase();
    if (value === undefined || hopByHopHeaders.has(normalizedKey)) continue;
    res.setHeader(key, value as string | string[]);
  }
};

export default async function handler(req: NextApiRequest, res: NextApiResponse) {
  const target = getTarget();
  const path = Array.isArray(req.query.path) ? req.query.path.join("/") : req.query.path || "";
  const query = req.url?.split("?")[1];
  const url = `${target}/${path}${query ? `?${query}` : ""}`;

  const headers = copyRequestHeaders(req);
  const data = req.method && ["GET", "HEAD"].includes(req.method) ? undefined : req;

  try {
    const response = await axios.request({
      url,
      method: req.method,
      headers,
      data,
      responseType: "stream",
      validateStatus: () => true,
      decompress: false,
      maxBodyLength: Infinity,
      maxContentLength: Infinity,
    });

    res.status(response.status);
    copyResponseHeaders(res, response.headers);
    if (req.method === "HEAD") {
      res.end();
      return;
    }
    await pipeline(response.data, res);
  } catch (err) {
    if (res.headersSent) {
      res.end();
      return;
    }
    res.status(502).json({ error: "proxy_failed" });
  }
}
