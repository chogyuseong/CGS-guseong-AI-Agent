// (tabs)처럼 괄호로 감싼 폴더는 주소에 나타나지 않는 "묶음"이다. /chat, /tasks 등이 이 레이아웃을 함께 쓴다.
// (Django에서 여러 뷰가 같은 base 템플릿을 extends 하는 것과 같다)
import type { ReactNode } from "react";
import { AuthGuard } from "@/components/AuthGuard";
import { BottomTabs } from "@/components/BottomTabs";

// h-dvh: 주소창·키보드가 나타나고 사라질 때 실제로 보이는 높이(dynamic viewport height)에 맞춘다.
// 위쪽 safe-area는 노치·상태 표시줄 영역이다.
// 넓은 화면(태블릿 가로·PC)에서는 본문을 읽기 좋은 폭(max-w-3xl, 768px)으로 가운데에 둔다(9단계 화면 폭 점검).
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
