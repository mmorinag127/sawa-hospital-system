/** @type {import('next').NextConfig} */
const useIdentityProxy = ["1", "true", "yes"].includes(
  String(process.env.API_PROXY_TARGET_REQUIRES_IDENTITY_TOKEN || "").toLowerCase()
);
const target = String(process.env.API_PROXY_TARGET || "");
const forceServerProxy = useIdentityProxy || target.includes("worker-stg");

const nextConfig = {
  experimental: {
    proxyTimeout: 900000,
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
    if (!target || forceServerProxy) return [];
    return [
      {
        source: "/api/:path*",
        destination: `${target}/:path*`,
      },
    ];
  },
};

module.exports = nextConfig;
