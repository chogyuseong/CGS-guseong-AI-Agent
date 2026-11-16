// "use client": 이 파일의 컴포넌트는 브라우저에서 실행된다는 표시.
// 표시가 없으면 서버에서만 그려지는 서버 컴포넌트다. (Django 템플릿=서버, 이 표시가 있는 파일=브라우저 JS)
"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
// ReactNode는 "화면에 그릴 수 있는 무엇이든"을 뜻하는 타입이다. (템플릿의 {% block %} 안에 들어갈 내용)
import type { ReactNode } from "react";

let browserQueryClient: QueryClient | undefined;

// Next.js 공식 안내(TanStack Query 가이드)대로: 서버 렌더링마다 새로 만들고, 브라우저에서는 하나를 재사용한다.
function getQueryClient(): QueryClient {
  if (typeof window === "undefined") return new QueryClient();
  browserQueryClient ??= new QueryClient();
  return browserQueryClient;
}

// Provider는 아래 모든 화면이 같은 QueryClient(캐시)를 쓰게 감싸는 컴포넌트다.
// (Django의 context processor가 모든 템플릿에 값을 넣어 주는 것과 비슷)
export function Providers({ children }: { children: ReactNode }) {
  return <QueryClientProvider client={getQueryClient()}>{children}</QueryClientProvider>;
}
