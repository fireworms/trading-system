import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // 개발 모드 좌하단 'N' 표시기 — 모바일에서 에이전트 채팅 입력창을 가린다.
  // 운영도 dev 서버(npm run dev)로 돌기 때문에 끈다. 빌드 오류 오버레이는 그대로 뜬다.
  devIndicators: false,
  // dev 서버 접속을 허용할 호스트 — 실제 값은 .env.local의 ALLOWED_DEV_ORIGINS (쉼표 구분)
  // 예: ALLOWED_DEV_ORIGINS=192.168.0.10,myhost.tailXXXX.ts.net
  allowedDevOrigins: (process.env.ALLOWED_DEV_ORIGINS ?? "")
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean),
};

export default nextConfig;
