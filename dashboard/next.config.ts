import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  async headers() {
    return [
      {
        // Never cache HTML pages — browser always fetches fresh HTML so it
        // gets the current chunk URLs after a container rebuild.
        source: "/(.*)",
        headers: [
          {
            key: "Cache-Control",
            value: "no-cache, no-store, must-revalidate",
          },
        ],
      },
      {
        // Allow long-term caching for hashed static assets (chunks, images).
        // These have content-hash in the filename so stale URLs are impossible.
        source: "/_next/static/(.*)",
        headers: [
          {
            key: "Cache-Control",
            value: "public, max-age=31536000, immutable",
          },
        ],
      },
    ];
  },
};

export default nextConfig;
