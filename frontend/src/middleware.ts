import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

// Canonicalize to the stable `a.run.app` domain (matches existing OAuth allowed origins).
const CANONICAL_HOST = "web-prod-avlnzjjrca-dt.a.run.app";
const ALT_HOSTS = new Set(["web-prod-167795504375.asia-northeast2.run.app"]);

export function middleware(request: NextRequest) {
  const host = (request.headers.get("host") || "").split(":")[0];
  if (ALT_HOSTS.has(host)) {
    const url = request.nextUrl.clone();
    url.protocol = "https:";
    url.host = CANONICAL_HOST;
    // Cloud Run may forward the internal container port (8080) in nextUrl.
    // Ensure we don't leak that port into public redirects.
    url.port = "";
    return NextResponse.redirect(url, 308);
  }
  return NextResponse.next();
}

export const config = {
  matcher: "/:path*",
};
