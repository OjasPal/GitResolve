import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  devIndicators: false,
  cacheComponents: true,
  experimental: { instantInsights: { validationLevel: "manual-warning" }, requestInsights: false },
  partialPrefetching: true,
};

export default nextConfig;
