// 맡은 일: 모든 화면에 공통으로 필요한 브라우저 쪽 준비를 한 곳에서 한다.
//   1) TanStack Query 캐시(QueryClient)를 화면 전체가 함께 쓰게 한다(lib/*.ts의 useQuery·useMutation 훅이 이 캐시를 쓴다).
//   2) 서비스 워커(public/sw.js, 푸시 수신·알림 클릭)를 등록한다(components/ServiceWorkerRegistrar).
// 부르는 곳: app/layout.tsx(루트 레이아웃)가 모든 화면을 이 컴포넌트로 감싼다.
// 고칠 때 주의: QueryClient에 전역 기본값(staleTime, retry 등)을 넣으면 모든 폴링·목록 화면의 동작이 바뀐다.
//   폴링 간격과 숨은 탭 멈춤은 lib/polling.ts에서 쿼리별로 정한다(docs/DECISIONS.md 9단계 보강 2026-11-18).

// "use client": 이 파일의 컴포넌트는 브라우저에서 실행된다는 표시.
// 표시가 없으면 서버에서만 그려지는 서버 컴포넌트다. (Django 템플릿=서버, 이 표시가 있는 파일=브라우저 JS)
"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
// ReactNode는 "화면에 그릴 수 있는 무엇이든"을 뜻하는 타입이다. (템플릿의 {% block %} 안에 들어갈 내용)
import type { ReactNode } from "react";
import { ServiceWorkerRegistrar } from "@/components/ServiceWorkerRegistrar";

// 브라우저에서 한 번 만든 캐시를 보관하는 자리. 모듈 변수라 페이지를 옮겨도 유지된다(새로 고침하면 비워진다).
let browserQueryClient: QueryClient | undefined;

/**
 * 지금 환경에 맞는 QueryClient를 돌려준다.
 *
 * Next.js 공식 안내(TanStack Query 가이드)대로: 서버 렌더링마다 새로 만들고, 브라우저에서는 하나를 재사용한다.
 * 서버에서 하나를 재사용하면 서로 다른 요청(다른 사용자)의 캐시가 섞일 수 있고,
 * 브라우저에서 매번 새로 만들면 다시 그릴 때마다 캐시가 사라진다.
 */
function getQueryClient(): QueryClient {
  if (typeof window === "undefined") return new QueryClient();
  browserQueryClient ??= new QueryClient();
  return browserQueryClient;
}

/**
 * 아래 모든 화면이 같은 QueryClient(캐시)를 쓰게 감싸는 컴포넌트다.
 * (Django의 context processor가 모든 템플릿에 값을 넣어 주는 것과 비슷)
 *
 * 부수 효과: ServiceWorkerRegistrar가 처음 그려질 때 서비스 워커를 등록한다(https·localhost에서만).
 */
export function Providers({ children }: { children: ReactNode }) {
  return (
    <QueryClientProvider client={getQueryClient()}>
      <ServiceWorkerRegistrar />
      {children}
    </QueryClientProvider>
  );
}
