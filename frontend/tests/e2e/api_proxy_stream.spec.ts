import { expect, test } from "@playwright/test";
import { createServer, type IncomingMessage, type ServerResponse } from "http";

const upstreamPort = 39091;
const upstreamBaseUrl = `http://127.0.0.1:${upstreamPort}`;

let server: ReturnType<typeof createServer> | null = null;
const requestBodies: Record<string, Buffer> = {};

const readBody = (req: IncomingMessage) =>
  new Promise<Buffer>((resolve, reject) => {
    const chunks: Buffer[] = [];
    req.on("data", (chunk) => chunks.push(Buffer.from(chunk)));
    req.on("end", () => resolve(Buffer.concat(chunks)));
    req.on("error", reject);
  });

const writeChunked = (res: ServerResponse, body: Buffer) =>
  new Promise<void>((resolve) => {
    const chunkSize = 64 * 1024;
    let offset = 0;
    const writeNext = () => {
      if (offset >= body.length) {
        res.end(resolve);
        return;
      }
      const nextOffset = Math.min(offset + chunkSize, body.length);
      const canContinue = res.write(body.subarray(offset, nextOffset));
      offset = nextOffset;
      if (canContinue) {
        setImmediate(writeNext);
      } else {
        res.once("drain", writeNext);
      }
    };
    writeNext();
  });

test.beforeAll(async () => {
  const largePayload = Buffer.alloc(2 * 1024 * 1024 + 123, "x");
  server = createServer(async (req, res) => {
    const url = new URL(req.url || "/", upstreamBaseUrl);
    if (url.pathname === "/large.bin") {
      res.writeHead(200, {
        "content-type": "application/octet-stream",
        "x-upstream-path": url.pathname,
        "x-upstream-query": url.searchParams.get("token") || "",
      });
      await writeChunked(res, largePayload);
      return;
    }
    if (url.pathname === "/upload" && req.method === "POST") {
      requestBodies.upload = await readBody(req);
      res.writeHead(201, { "content-type": "application/json" });
      res.end(JSON.stringify({ received: requestBodies.upload.length }));
      return;
    }
    res.writeHead(404, { "content-type": "application/json" });
    res.end(JSON.stringify({ detail: "not found" }));
  });
  await new Promise<void>((resolve) => server?.listen(upstreamPort, "127.0.0.1", resolve));
});

test.afterAll(async () => {
  await new Promise<void>((resolve, reject) => {
    if (!server) {
      resolve();
      return;
    }
    server.close((err) => (err ? reject(err) : resolve()));
  });
});

test("api proxy streams large binary responses without truncating headers or body", async ({ request }) => {
  const response = await request.get("/api/large.bin?token=e2e");

  expect(response.status()).toBe(200);
  expect(response.headers()["content-type"]).toContain("application/octet-stream");
  expect(response.headers()["x-upstream-path"]).toBe("/large.bin");
  expect(response.headers()["x-upstream-query"]).toBe("e2e");
  const body = await response.body();
  expect(body.length).toBe(2 * 1024 * 1024 + 123);
  expect(body.subarray(0, 4).toString()).toBe("xxxx");
});

test("api proxy forwards request bodies without buffering through Next bodyParser", async ({ request }) => {
  const uploadBody = Buffer.alloc(1024 * 1024 + 321, "u");
  const response = await request.post("/api/upload", {
    data: uploadBody,
    headers: { "content-type": "application/octet-stream" },
  });

  expect(response.status()).toBe(201);
  await expect(response.json()).resolves.toEqual({ received: uploadBody.length });
  expect(requestBodies.upload?.length).toBe(uploadBody.length);
  expect(requestBodies.upload?.subarray(-4).toString()).toBe("uuuu");
});
