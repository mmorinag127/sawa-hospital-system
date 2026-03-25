import type { NextApiRequest, NextApiResponse } from "next";
import axios from "axios";

const getTarget = () =>
  process.env.API_PROXY_TARGET || "https://worker-prod-avlnzjjrca-dt.a.run.app";

const shouldUseIdentityProxy = () =>
  ["1", "true", "yes"].includes((process.env.API_PROXY_TARGET_REQUIRES_IDENTITY_TOKEN || "").toLowerCase()) ||
  getTarget().includes("worker-stg");

const getTargetAudience = () => process.env.API_PROXY_TARGET_AUDIENCE || getTarget();

const fetchIdentityToken = async (audience: string) => {
  const metadataUrl =
    "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/identity";
  const response = await fetch(
    `${metadataUrl}?audience=${encodeURIComponent(audience)}`,
    {
      headers: {
        "Metadata-Flavor": "Google",
      },
    }
  );
  if (!response.ok) {
    throw new Error(`identity_token_failed:${response.status}`);
  }
  return (await response.text()).trim();
};

export const config = {
  api: {
    bodyParser: false,
  },
};

const readBody = (req: NextApiRequest): Promise<Buffer> =>
  new Promise((resolve, reject) => {
    const chunks: Buffer[] = [];
    req.on("data", (chunk) => chunks.push(Buffer.from(chunk)));
    req.on("end", () => resolve(Buffer.concat(chunks)));
    req.on("error", reject);
  });

export default async function handler(req: NextApiRequest, res: NextApiResponse) {
  const target = getTarget();
  const path = Array.isArray(req.query.path) ? req.query.path.join("/") : req.query.path || "";
  const query = req.url?.split("?")[1];
  const url = `${target}/${path}${query ? `?${query}` : ""}`;
  const authMode = shouldUseIdentityProxy() ? "identity" : req.headers.authorization ? "passthrough" : "none";

  res.setHeader("x-sawa-api-proxy", "route-hit");
  res.setHeader("x-sawa-api-proxy-mode", authMode);
  res.setHeader("x-sawa-api-proxy-path", path || "root");

  const headers: Record<string, string> = {};
  let originalAuthorization = "";
  for (const [key, value] of Object.entries(req.headers)) {
    if (!value) continue;
    if (key.toLowerCase() === "authorization") {
      originalAuthorization = Array.isArray(value) ? value.join(",") : value;
      continue;
    }
    if (Array.isArray(value)) {
      headers[key] = value.join(",");
    } else {
      headers[key] = value;
    }
  }
  delete headers.host;

  if (originalAuthorization) {
    headers["x-app-authorization"] = originalAuthorization;
  }

  if (shouldUseIdentityProxy()) {
    try {
      const identityToken = await fetchIdentityToken(getTargetAudience());
      headers["x-serverless-authorization"] = `Bearer ${identityToken}`;
      if (originalAuthorization) {
        headers.authorization = originalAuthorization;
      }
      res.setHeader("x-sawa-api-proxy-auth", "identity-token+x-serverless-authorization");
    } catch (err) {
      res.setHeader("x-sawa-api-proxy-auth", "identity-token-failed");
      res.status(502).json({ error: "proxy_identity_failed" });
      return;
    }
  } else if (originalAuthorization) {
    headers.authorization = originalAuthorization;
    res.setHeader("x-sawa-api-proxy-auth", "passthrough-authorization");
  } else {
    res.setHeader("x-sawa-api-proxy-auth", "none");
  }

  const data = req.method && ["GET", "HEAD"].includes(req.method) ? undefined : await readBody(req);

  try {
    const response = await axios.request({
      url,
      method: req.method,
      headers,
      data,
      responseType: "arraybuffer",
      validateStatus: () => true,
    });

    res.status(response.status);
    res.setHeader("x-sawa-api-upstream-status", String(response.status));
    for (const [key, value] of Object.entries(response.headers)) {
      if (value === undefined) continue;
      res.setHeader(key, value as string);
    }
    res.send(Buffer.from(response.data));
  } catch (err) {
    res.setHeader("x-sawa-api-proxy-auth", "upstream-request-failed");
    res.status(502).json({ error: "proxy_failed" });
  }
}
