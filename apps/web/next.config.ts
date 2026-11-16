import { networkInterfaces } from "node:os";
import type { NextConfig } from "next";

// FastAPI 서버 주소. 브라우저는 이 주소를 모르고 항상 같은 출처(/api/...)로만 호출한다.
// 그래서 CORS 설정이 필요 없다. (Django에서 nginx가 /api를 뒤쪽 서버로 넘기는 것과 같은 역할)
const API_ORIGIN = "http://localhost:8000";

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

const nextConfig: NextConfig = {
  // Cache Components(서버 화면을 캐시하는 렌더링 방식)는 끈다(2026-11-16 결정).
  // 이 앱은 로그인 뒤에만 쓰고 화면 데이터를 브라우저에서 TanStack Query로 가져오므로 얻는 것이 적고,
  // 켜 두면 클라이언트 쿼리를 Suspense로 감싸야 하고 개발 서버에 instant navigation 검증 오류가 찍혔다.
  // create-next-app 16은 둘 다 true로 만든다. 다음 메이저에서 항상 켜질 예정이라고 문서에 적혀 있다.
  cacheComponents: false,
  partialPrefetching: false,
  // 개발 서버의 교차 출처 허용(개발 모드 전용). 호스트 이름만 적는다(포트, http:// 없이).
  allowedDevOrigins: getLanAddresses(),
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
