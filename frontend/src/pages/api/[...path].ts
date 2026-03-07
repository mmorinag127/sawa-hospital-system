import type { NextApiRequest, NextApiResponse } from "next";
import axios from "axios";

const getTarget = () =>
  process.env.API_PROXY_TARGET || "https://worker-prod-avlnzjjrca-dt.a.run.app";

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

  const headers: Record<string, string> = {};
  for (const [key, value] of Object.entries(req.headers)) {
    if (!value) continue;
    if (Array.isArray(value)) {
      headers[key] = value.join(",");
    } else {
      headers[key] = value;
    }
  }
  delete headers.host;

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
    for (const [key, value] of Object.entries(response.headers)) {
      if (value === undefined) continue;
      res.setHeader(key, value as string);
    }
    res.send(Buffer.from(response.data));
  } catch (err) {
    res.status(502).json({ error: "proxy_failed" });
  }
}
