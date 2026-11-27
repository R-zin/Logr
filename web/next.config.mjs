/** @type {import('next').NextConfig} */
const isProd = process.env.NODE_ENV === "production";

const nextConfig = {
  reactStrictMode: true,
  trailingSlash: true,
  images: { unoptimized: true },
  // Production builds are a static export (web/out) served by `logr serve`.
  // Rewrites are not allowed with `output: "export"`, so the dev proxy to the
  // Python API only exists in development.
  ...(isProd
    ? { output: "export" }
    : {
        // trailingSlash would otherwise redirect /api/x to /api/x/ before the
        // rewrite runs; FastAPI routes have no trailing slash.
        skipTrailingSlashRedirect: true,
        async rewrites() {
          const api = process.env.LOGR_API_URL || "http://127.0.0.1:8000";
          return [{ source: "/api/:path*", destination: `${api}/api/:path*` }];
        },
      }),
};

export default nextConfig;
