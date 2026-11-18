// Next.js 설정: /api 프록시(rewrites), 개발 서버 교차 출처 허용, 서비스 워커 응답 헤더.
// 근거: DECISIONS 2026-11-16(API는 127.0.0.1에만 열고 폰은 Next rewrites로만 닿는다, Cache Components 끔),
// 2026-11-17 화면 절(/sw.js 헤더, DEV_TUNNEL_HOST만 허용).
//
// 고칠 때 주의:
// - 로그인 Origin 검사는 API 서버의 APP_ORIGIN과 비교한다. 접속 주소(폰 IP·터널)를 바꾸면 루트 .env의 APP_ORIGIN도
//   바꿔야 로그인된다(apps/web/README.md "폰에서 접속하기").
// - /sw.js 헤더는 lib/push.ts의 SERVICE_WORKER_URL과 같은 경로여야 한다.
// - next.config.ts는 서버(개발 서버·빌드)를 다시 시작해야 반영된다.
import { networkInterfaces } from "node:os";
import type { NextConfig } from "next";

// FastAPI 서버 주소. 브라우저는 이 주소를 모르고 항상 같은 출처(/api/...)로만 호출한다.
// 그래서 CORS 설정이 필요 없다. (Django에서 nginx가 /api를 뒤쪽 서버로 넘기는 것과 같은 역할)
// 배포(docs/DEPLOY.md)에서는 빌드 환경변수 API_ORIGIN으로 넣는다(예: Railway 사설망 http://api.railway.internal:8080).
// rewrites는 빌드 때 값을 읽으므로 바꾸면 다시 빌드한다. 끝의 /는 뗀다.
const API_ORIGIN = (process.env.API_ORIGIN?.trim() || "http://localhost:8000").replace(/\/+$/, "");

// 이 PC의 사설 IPv4 주소 목록(예: 192.168.0.12).
// 폰이 같은 와이파이에서 http://<이 주소>:3000 으로 열 때 개발 서버가 막지 않도록 허용 목록에 넣는다.
// 주소가 바뀌어도 고칠 필요가 없게 실행할 때마다 계산한다.
function getLanAddresses(): string[] {
  const addresses: string[] = [];
  for (const entries of Object.values(networkInterfaces())) {
    for (const entry of entries ?? []) {
      if (entry.family === "IPv4" && !entry.internal) {
        addresses.push(entry.address);
      }
    }
  }
  return addresses;
}

// 폰에서 푸시를 확인할 때 쓰는 https 터널 주소의 호스트 이름(예: abc-def.trycloudflare.com). 개발 서버는 처음 보는
// 출처의 개발용 요청을 막으므로 터널을 쓸 때만 환경변수로 넣는다(docs/PUSH-CHECK.md). 늘 열어 두지 않으려고
// 와일드카드(*.trycloudflare.com)는 쓰지 않는다. 호스트 이름 모양이 아니면 무시한다.
// 막는 사고: 와일드카드를 늘 열어 두면 그 도메인의 아무 사이트나 개발 서버의 개발용 자원을 요청할 수 있다.
function getTunnelHosts(): string[] {
  const host = process.env.DEV_TUNNEL_HOST?.trim().toLowerCase();
  return host && /^[a-z0-9-]+(\.[a-z0-9-]+)+$/.test(host) ? [host] : [];
}

const nextConfig: NextConfig = {
  // Cache Components(서버 화면을 캐시하는 렌더링 방식)는 끈다(2026-11-16 결정).
  // 이 앱은 로그인 뒤에만 쓰고 화면 데이터를 브라우저에서 TanStack Query로 가져오므로 얻는 것이 적고,
  // 켜 두면 클라이언트 쿼리를 Suspense로 감싸야 하고 개발 서버에 instant navigation 검증 오류가 찍혔다.
  // create-next-app 16은 둘 다 true로 만든다. 다음 메이저에서 항상 켜질 예정이라고 문서에 적혀 있다.
  cacheComponents: false,
  partialPrefetching: false,
  // 개발 서버의 교차 출처 허용(개발 모드 전용). 호스트 이름만 적는다(포트, http:// 없이).
  allowedDevOrigins: [...getLanAddresses(), ...getTunnelHosts()],
  // 개발 모드 표시(N 배지)의 기본 위치는 왼쪽 아래라 360px 화면에서 하단 탭을 가린다. 오른쪽 위로 옮긴다.
  devIndicators: { position: "top-right" },
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
  // 서비스 워커 파일 응답 헤더(Next.js PWA 가이드). 브라우저가 sw.js를 캐시하지 않고 매번 새로 확인하게 하고,
  // 서비스 워커 안에서는 같은 출처의 스크립트만 쓸 수 있게 한다.
  async headers() {
    return [
      {
        source: "/sw.js",
        headers: [
          { key: "Content-Type", value: "application/javascript; charset=utf-8" },
          { key: "Cache-Control", value: "no-cache, no-store, must-revalidate" },
          { key: "Content-Security-Policy", value: "default-src 'self'; script-src 'self'" },
        ],
      },
    ];
  },
  // /api/* 요청을 FastAPI로 넘긴다. (Django urls.py의 include()처럼 경로를 다른 곳으로 연결)
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${API_ORIGIN}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
