import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

// Canonicalize to the stable `a.run.app` domain (matches existing OAuth allowed origins).
const CANONICAL_HOST = "web-prod-avlnzjjrca-dt.a.run.app";
const ALT_HOSTS = new Set(["web-prod-167795504375.asia-northeast2.run.app"]);

export function proxy(request: NextRequest) {
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
  const pathname = request.nextUrl.pathname;
  if (pathname === "/hospital") {
    const url = request.nextUrl.clone();
    url.pathname = "/hospital-dashboard";
    return NextResponse.rewrite(url);
  }
  if (pathname.startsWith("/hospital/")) {
    const url = request.nextUrl.clone();
    url.pathname = pathname.slice("/hospital".length) || "/";
    return NextResponse.rewrite(url);
  }
  const hospitalRoots = new Set([
    "orders", "weekly-orders", "pdf-upload", "daily-delivery-notes", "totals",
    "facilities", "facility-master", "facility-orders", "menus", "base-menus",
    "menu-masters", "menu-rules", "shipping", "shipping-history", "order-forms",
    "weekly-weight-output", "ocr-facilities", "ocr-queue", "ocr-results",
    "ocr-templates", "ocr-training-data", "system-process-logs", "system-status",
    "manual-library",
  ]);
  const root = pathname.split("/")[1] || "";
  if (hospitalRoots.has(root)) {
    const url = request.nextUrl.clone();
    url.pathname = `/hospital${pathname}`;
    return NextResponse.redirect(url, 308);
  }
  return NextResponse.next();
}

export const config = {
  matcher: "/:path*",
};
