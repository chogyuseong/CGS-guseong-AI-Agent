// 맡은 일: 외부 서비스 연결 화면(/connections, 4단계·7단계). Google 연결 카드(캘린더 연결·해제·다시 연결,
//   메일 읽기 권한과 메일 데이터 처리 동의)는 components/GoogleConnection이, Google 동의 화면에서 돌아왔을 때의
//   결과 안내(?google=connected|error&reason=…)는 components/CallbackNotice가 그린다.
// 들어오는 곳: 설정 탭의 'Google 계정 연결' 카드, 그리고 서버의 OAuth 콜백 302
//   ({APP_ORIGIN}/connections?google=…, apps/api/app/routers/connections.py의 CONNECTIONS_PAGE).
// 근거: docs/DECISIONS.md 4단계 "화면(apps/web)"(2026-11-17: (tabs) 아래 둠, 쿼리 안내는 한 번 읽고 지움, Suspense).
// 고칠 때 주의: 경로를 바꾸면 서버 CONNECTIONS_PAGE(콜백·미로그인 302 대상), lib/connections.ts의 CONNECTIONS_PATH
//   (안내를 읽은 뒤 쿼리를 지우는 router.replace 대상), BottomTabs의 설정 탭 children, 설정 탭 링크,
//   e2e(connections·dark-mode·widths spec)를 함께 고친다. h1 "외부 서비스 연결"은 dark-mode·widths spec이 확인한다.
import { ChevronLeft } from "lucide-react";
import Link from "next/link";
import { Suspense } from "react";
import { CallbackNotice } from "@/components/CallbackNotice";
import { GoogleConnectionCard } from "@/components/GoogleConnection";

// 외부 서비스 연결 화면(명세 apps/web 구조의 connections). 설정 탭에서 들어온다.
// 로그인 확인과 하단 탭은 (tabs)/layout.tsx가 맡는다.
// CallbackNotice는 주소의 쿼리를 읽으므로(useSearchParams) Suspense로 감싼다(Next.js 문서 권장).
// fallback={null}: 안내는 콜백으로 돌아온 경우에만 있으므로 기다리는 동안 자리를 비워 둔다.
export default function ConnectionsPage() {
  return (
    <section className="flex flex-1 flex-col gap-3 overflow-y-auto p-4">
      {/* 하위 화면이라 설정으로 돌아가는 링크를 둔다. min-h-11: 누르기 쉬운 44px 높이. */}
      <Link
        href="/settings"
        className="-ml-2 flex min-h-11 w-fit items-center gap-1 px-2 text-sm text-muted-foreground"
      >
        <ChevronLeft aria-hidden="true" className="size-4" />
        설정
      </Link>
      <h1 className="text-lg font-semibold">외부 서비스 연결</h1>
      <Suspense fallback={null}>
        <CallbackNotice />
      </Suspense>
      <GoogleConnectionCard />
    </section>
  );
}
