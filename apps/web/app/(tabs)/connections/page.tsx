import { ChevronLeft } from "lucide-react";
import Link from "next/link";
import { Suspense } from "react";
import { CallbackNotice } from "@/components/CallbackNotice";
import { GoogleConnectionCard } from "@/components/GoogleConnection";

// 외부 서비스 연결 화면(명세 apps/web 구조의 connections). 설정 탭에서 들어온다.
// 로그인 확인과 하단 탭은 (tabs)/layout.tsx가 맡는다.
// CallbackNotice는 주소의 쿼리를 읽으므로(useSearchParams) Suspense로 감싼다(Next.js 문서 권장).
export default function ConnectionsPage() {
  return (
    <section className="flex flex-1 flex-col gap-3 overflow-y-auto p-4">
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
