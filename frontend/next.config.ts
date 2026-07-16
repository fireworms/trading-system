import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // dev 서버 접속을 허용할 호스트 — 실제 값은 .env.local의 ALLOWED_DEV_ORIGINS (쉼표 구분)
  // 예: ALLOWED_DEV_ORIGINS=192.168.0.10,myhost.tailXXXX.ts.net
  allowedDevOrigins: (process.env.ALLOWED_DEV_ORIGINS ?? "")
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean),
};

export default nextConfig;
