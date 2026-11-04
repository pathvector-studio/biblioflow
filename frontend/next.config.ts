import type { NextConfig } from "next";

// standalone ビルドでは rewrites はビルド時に評価される。
// そのため API_INTERNAL_URL はビルド引数として渡す。
const apiInternalUrl = process.env.API_INTERNAL_URL || "http://biblioflow-api:8000";

const nextConfig: NextConfig = {
  output: "standalone",
  poweredByHeader: false,
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${apiInternalUrl}/api/:path*` }];
  },
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
        ],
      },
      {
        // pdf.js の worker。URL に pdfjs のバージョンを付けて読む（pdf-pane.tsx）ので、更新すれば URL が変わる
        source: "/pdf.worker.min.mjs",
        headers: [{ key: "Cache-Control", value: "public, max-age=31536000, immutable" }],
      },
    ];
  },
};

export default nextConfig;
