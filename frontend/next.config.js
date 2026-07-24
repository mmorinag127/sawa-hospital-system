/** @type {import('next').NextConfig} */
const nextConfig = {
  experimental: {
    proxyTimeout: 900000,
  },
  turbopack: {
    root: __dirname,
  },
  async headers() {
    return [
      {
        source: "/((?!api|_next/static|_next/image|favicon.ico).*)",
        headers: [
          {
            key: "Cache-Control",
            value: "no-store, no-cache, must-revalidate, proxy-revalidate, max-age=0",
          },
          {
            key: "Pragma",
            value: "no-cache",
          },
          {
            key: "Expires",
            value: "0",
          },
        ],
      },
    ];
  },
  async rewrites() {
    const target = process.env.API_PROXY_TARGET;
    const shiftTarget = process.env.SHIFT_WEB_PROXY_TARGET;
    const routes = [];
    if (shiftTarget) {
      routes.push(
        { source: "/shift-assets/:path*", destination: `${shiftTarget}/:path*` },
        { source: "/shift-manual/:path*", destination: `${shiftTarget}/manual/:path*` },
        { source: "/shift/api/:path*", destination: `${shiftTarget}/api/:path*` },
        { source: "/shift/:path*", destination: `${shiftTarget}/shift/:path*` },
      );
    }
    if (!target) return routes;
    return [
      ...routes,
      {
        source: "/api/hospital/:path*",
        destination: `${target}/:path*`,
      },
      {
        source: "/api/:path*",
        destination: `${target}/:path*`,
      },
    ];
  },
};

module.exports = nextConfig;
