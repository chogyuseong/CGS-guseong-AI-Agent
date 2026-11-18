// 맡은 일: 설정 탭(/settings). 알림(웹 푸시) 켜기·끄기(PushSettings), 하위 화면으로 가는 바로가기 카드
//   (/connections: Google 계정 연결, /usage: 비용), 데이터 보관 안내(DataRetentionNotice)를 세로로 둔다.
// 하위 화면 /connections·/usage에서도 하단 탭은 '설정'을 현재 탭으로 표시한다(BottomTabs의 children).
// 근거: docs/DECISIONS.md 4단계 "화면(apps/web)"(연결 화면은 설정 탭에서 들어감), 9단계 "화면(비용)"(2026-11-17).
// 고칠 때 주의:
// - 바로가기를 더하거나 href를 바꾸면 BottomTabs의 설정 탭 children도 고친다(안 그러면 그 화면에서 현재 탭 표시가 사라진다).
// - 카드 제목 문구("Google 계정 연결", "비용")는 e2e가 링크 이름으로 찾는다(settings·connections·usage spec).
//   h1 "설정"은 dark-mode·widths spec이 제목으로 확인한다.
import { ChevronRight, Coins, Link2, type LucideIcon } from "lucide-react";
import Link from "next/link";
import { DataRetentionNotice } from "@/components/DataRetentionNotice";
import { PushSettings } from "@/components/PushSettings";

// 설정 탭. 알림(웹 푸시) 영역과 외부 서비스 연결(4단계: Google)·비용(9단계) 화면으로 가는 링크, 데이터 보관 안내(5단계 보강)가 있다.
export default function SettingsPage() {
  return (
    <section className="flex flex-1 flex-col gap-3 overflow-y-auto p-4">
      <h1 className="text-lg font-semibold">설정</h1>
      <PushSettings />
      <SettingsLink
        href="/connections"
        icon={Link2}
        title="Google 계정 연결"
        description="캘린더 일정·메일 읽기 권한 관리"
      />
      <SettingsLink
        href="/usage"
        icon={Coins}
        title="비용"
        description="일별 토큰과 비용, 용도별 합계, 오늘 남은 한도"
      />
      <DataRetentionNotice />
    </section>
  );
}

type SettingsLinkProps = { href: string; icon: LucideIcon; title: string; description: string };

/**
 * 설정 탭의 바로가기 카드(아이콘 + 제목·설명 두 줄 + 오른쪽 화살표). 카드 전체가 링크라 어디를 눌러도 이동한다.
 *
 * props: href(앱 안 경로), icon(lucide 아이콘, 장식이라 aria-hidden), title·description(링크의 접근성 이름이 된다).
 * min-h-14: 손가락으로 누르기 충분한 높이(44px 이상). shrink-0: 스크롤되는 세로 목록에서 카드가 줄어들어
 * 설명이 테두리 밖으로 넘치던 문제(2026-11-18, e2e/settings.spec.ts가 확인)를 막는다.
 */
function SettingsLink({ href, icon: Icon, title, description }: SettingsLinkProps) {
  return (
    <Link
      href={href}
      className="flex min-h-14 min-w-0 shrink-0 items-center gap-3 rounded-xl border bg-card p-4 text-card-foreground"
    >
      <Icon aria-hidden="true" className="size-5 shrink-0" />
      <span className="flex min-w-0 flex-1 flex-col">
        <span className="text-base font-semibold">{title}</span>
        <span className="text-sm text-muted-foreground">{description}</span>
      </span>
      <ChevronRight aria-hidden="true" className="size-5 shrink-0 text-muted-foreground" />
    </Link>
  );
}
