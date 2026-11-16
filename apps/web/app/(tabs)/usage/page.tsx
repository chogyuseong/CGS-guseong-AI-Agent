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
