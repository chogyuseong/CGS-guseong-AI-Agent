import { ChevronRight, Link2 } from "lucide-react";
import Link from "next/link";
import { PushSettings } from "@/components/PushSettings";

// 설정 탭. 알림(웹 푸시) 영역과 외부 서비스 연결(4단계: Google) 화면으로 가는 링크가 있다.
export default function SettingsPage() {
  return (
    <section className="flex flex-1 flex-col gap-3 overflow-y-auto p-4">
      <h1 className="text-lg font-semibold">설정</h1>
      <PushSettings />
      <Link
        href="/connections"
        className="flex min-h-14 min-w-0 items-center gap-3 rounded-xl border bg-card p-4 text-card-foreground"
      >
        <Link2 aria-hidden="true" className="size-5 shrink-0" />
        <span className="flex min-w-0 flex-1 flex-col">
          <span className="text-base font-semibold">Google 계정 연결</span>
          <span className="text-sm text-muted-foreground">캘린더 일정·메일 읽기 권한 관리</span>
        </span>
        <ChevronRight aria-hidden="true" className="size-5 shrink-0 text-muted-foreground" />
      </Link>
    </section>
  );
}
