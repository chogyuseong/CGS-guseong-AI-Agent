// 맡은 일: 하단 탭이 있는 모든 화면의 공통 틀. 로그인 확인(AuthGuard)과 하단 탭(BottomTabs)을 한 번만 붙인다.
// 경로: /chat, /chat/[id], /tasks, /automation, /memory, /settings, /connections, /usage. /login은 이 묶음 밖이다.
// (tabs)처럼 괄호로 감싼 폴더는 주소에 나타나지 않는 "묶음"이다. /chat, /tasks 등이 이 레이아웃을 함께 쓴다.
// (Django에서 여러 뷰가 같은 base 템플릿을 extends 하는 것과 같다)
// 근거: docs/DECISIONS.md 9단계 "화면 점검"(2026-11-17, 고친 것 1: 넓은 화면에서 본문을 max-w-3xl로 모음).
//
// 고칠 때 주의:
// - 이 묶음에 화면을 더하거나 경로를 바꾸면 함께 고칠 곳:
//   components/BottomTabs.tsx의 TAB_ITEMS(탭 순서·children: 설정 탭 아래 /connections·/usage),
//   푸시 알림 URL(서버 apps/api/app/agent/runner.py·automation/check.py가 "/chat/{대화 id}"를 보내고
//   apps/api/app/push.py의 is_internal_url이 앱 내부 경로만 허용), public/sw.js(DEFAULT_PATH "/chat"과 safePath),
//   Google 연결 콜백(apps/api/app/routers/connections.py의 CONNECTIONS_PAGE "/connections", lib/connections.ts의 CONNECTIONS_PATH),
//   e2e(dark-mode·widths spec의 경로·h1 제목 표, login.spec의 탭 이름 표).
// - main의 max-w-3xl을 바꾸면 BottomTabs의 탭 목록 폭과 e2e/width-audit.ts(768 초과 폭에서 800px 이하 가운데)를 맞춘다.
import type { ReactNode } from "react";
import { AuthGuard } from "@/components/AuthGuard";
import { BottomTabs } from "@/components/BottomTabs";

/**
 * 로그인한 사용자에게만 본문과 하단 탭을 보여 준다. 확인 중·미로그인·서버 오류 처리는 AuthGuard가 한다
 * (미로그인이면 /login으로 보낸다). 탭 막대도 AuthGuard 안에 있어 로그인 전에는 보이지 않는다.
 *
 * h-dvh: 주소창·키보드가 나타나고 사라질 때 실제로 보이는 높이(dynamic viewport height)에 맞춘다.
 * 위쪽 safe-area는 노치·상태 표시줄 영역이다(아래쪽은 BottomTabs가 맡는다).
 * 넓은 화면(태블릿 가로·PC)에서는 본문을 읽기 좋은 폭(max-w-3xl, 768px)으로 가운데에 둔다(9단계 화면 폭 점검).
 * min-h-0 + flex-1: 각 화면의 overflow-y-auto 영역이 탭 막대를 밀어내지 않고 안에서 스크롤되게 한다.
 */
export default function TabsLayout({ children }: { children: ReactNode }) {
  return (
    <div className="flex h-dvh flex-col pt-[env(safe-area-inset-top)]">
      <AuthGuard>
        <main className="mx-auto flex min-h-0 w-full max-w-3xl flex-1 flex-col">{children}</main>
        <BottomTabs />
      </AuthGuard>
    </div>
  );
}
