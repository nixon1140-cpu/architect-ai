import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // backend/Dockerfile が standalone 出力を前提としているため必須（仕様書 Step1）。
  output: "standalone",
};

export default nextConfig;
