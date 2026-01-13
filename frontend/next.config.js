/** @type {import('next').NextConfig} */
const nextConfig = {
  experimental: {
    proxyTimeout: 900000,
  },
  async rewrites() {
    const target = process.env.API_PROXY_TARGET;
    if (!target) return [];
    return [
      {
        source: "/api/:path*",
        destination: `${target}/:path*`,
      },
    ];
  },
};

module.exports = nextConfig;
