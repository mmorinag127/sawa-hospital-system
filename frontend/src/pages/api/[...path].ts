import type { NextApiRequest, NextApiResponse } from "next";
import axios from "axios";
import { Transform } from "stream";
import { pipeline } from "stream/promises";

const getTarget = () => {
  const target = process.env.API_PROXY_TARGET?.trim();
  if (!target) {
    throw new Error("api_proxy_target_missing");
  }
  return target;
};
const PROXY_MAX_BODY_BYTES = Number(process.env.API_PROXY_MAX_BODY_BYTES || 25 * 1024 * 1024);
const PROXY_MAX_RESPONSE_BYTES = Number(process.env.API_PROXY_MAX_RESPONSE_BYTES || 100 * 1024 * 1024);

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

const contentLengthExceeds = (value: string | string[] | undefined, limit: number) => {
  const raw = Array.isArray(value) ? value[0] : value;
  const parsed = Number(raw || 0);
  return Number.isFinite(parsed) && parsed > limit;
};

const limitStream = (limit: number) => {
  let seen = 0;
  return new Transform({
    transform(chunk, _encoding, callback) {
      seen += Buffer.byteLength(chunk);
      if (seen > limit) {
        callback(new Error("stream_size_limit_exceeded"));
        return;
      }
      callback(null, chunk);
    },
  });
};

export default async function handler(req: NextApiRequest, res: NextApiResponse) {
  if (contentLengthExceeds(req.headers["content-length"], PROXY_MAX_BODY_BYTES)) {
    res.status(413).json({ error: "request_too_large" });
    return;
  }
  try {
    const target = getTarget();
    const path = Array.isArray(req.query.path) ? req.query.path.join("/") : req.query.path || "";
    const query = req.url?.split("?")[1];
    const url = `${target}/${path}${query ? `?${query}` : ""}`;

    const headers = copyRequestHeaders(req);
    const data = req.method && ["GET", "HEAD"].includes(req.method) ? undefined : req.pipe(limitStream(PROXY_MAX_BODY_BYTES));

    const response = await axios.request({
      url,
      method: req.method,
      headers,
      data,
      responseType: "stream",
      validateStatus: () => true,
      decompress: false,
      maxBodyLength: PROXY_MAX_BODY_BYTES,
      maxContentLength: PROXY_MAX_RESPONSE_BYTES,
      maxRedirects: 0,
    });

    if (contentLengthExceeds(response.headers["content-length"] as string | string[] | undefined, PROXY_MAX_RESPONSE_BYTES)) {
      res.status(502).json({ error: "upstream_response_too_large" });
      return;
    }
    res.status(response.status);
    copyResponseHeaders(res, response.headers);
    if (req.method === "HEAD") {
      res.end();
      return;
    }
    await pipeline(response.data, limitStream(PROXY_MAX_RESPONSE_BYTES), res);
  } catch (err) {
    if (res.headersSent) {
      res.end();
      return;
    }
    res.status(502).json({ error: "proxy_failed" });
  }
}
