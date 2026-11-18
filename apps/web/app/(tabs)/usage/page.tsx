// 맡은 일: 비용 화면(/usage, 9단계 2번). 기간 선택(7·14·30일), 오늘 한도 카드, 일별 표, 용도별·모델별 합계는
//   components/UsageReport가 GET /api/usage/daily 한 번으로 그린다. 보기만 하는 화면이다.
// 들어오는 곳: 설정 탭의 '비용' 카드. 하단 탭은 '설정'이 현재 탭이다(BottomTabs의 children).
// 근거: docs/DECISIONS.md 9단계 "화면(비용)"(2026-11-17), 9단계 보강(2026-11-18: API를 /api/usage/daily로 옮김).
// 고칠 때 주의: 경로를 바꾸면 BottomTabs의 설정 탭 children, 설정 탭 링크, e2e(usage·dark-mode·widths spec)를 고친다.
//   h1 "비용"은 usage·dark-mode·widths spec이 제목(level 1)으로 확인한다.
import { ChevronLeft } from "lucide-react";
import Link from "next/link";
import { UsageReport } from "@/components/UsageReport";

// 비용 화면(명세 apps/web 구조의 usage, 9단계 2번). 설정 탭에서 들어온다.
// 로그인 확인과 하단 탭은 (tabs)/layout.tsx가 맡는다.
export default function UsagePage() {
  return (
    <section
      aria-labelledby="usage-title"
      className="flex flex-1 flex-col gap-3 overflow-y-auto p-4"
    >
      {/* 하위 화면이라 설정으로 돌아가는 링크를 둔다. min-h-11: 누르기 쉬운 44px 높이. */}
      <Link
        href="/settings"
        className="-ml-2 flex min-h-11 w-fit items-center gap-1 px-2 text-sm text-muted-foreground"
      >
        <ChevronLeft aria-hidden="true" className="size-4" />
        설정
      </Link>
      <h1 id="usage-title" className="text-lg font-semibold">
        비용
      </h1>
      <UsageReport />
    </section>
  );
}
