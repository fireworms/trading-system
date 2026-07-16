import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // dev 서버 접속을 허용할 호스트 — 새 접속 경로(도메인/IP) 추가 시 여기도 등록
  allowedDevOrigins: [
    '192.168.0.10',                // 내부 LAN
    'myhost.tail0000.ts.net',  // Tailscale MagicDNS
    '100.64.0.1',                 // Tailscale IP
  ],
};

export default nextConfig;
