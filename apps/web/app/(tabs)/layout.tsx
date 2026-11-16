// (tabs)처럼 괄호로 감싼 폴더는 주소에 나타나지 않는 "묶음"이다. /chat, /tasks 등이 이 레이아웃을 함께 쓴다.
// (Django에서 여러 뷰가 같은 base 템플릿을 extends 하는 것과 같다)
import { type ReactNode, Suspense } from "react";
import { AuthGuard } from "@/components/AuthGuard";
import { BottomTabs } from "@/components/BottomTabs";

// h-dvh: 주소창·키보드가 나타나고 사라질 때 실제로 보이는 높이(dynamic viewport height)에 맞춘다.
// 위쪽 safe-area는 노치·상태 표시줄 영역이다.
export default function TabsLayout({ children }: { children: ReactNode }) {
  return (
    <div className="flex h-dvh flex-col pt-[env(safe-area-inset-top)]">
      {/* Suspense: 안쪽이 준비될 때까지 fallback을 보여 준다. Cache Components 설정에서는
          브라우저에서 데이터를 가져오는 부분을 이 경계 안에 두어야 한다(Next.js 공식 안내). */}
      <Suspense fallback={<p className="flex-1 p-4 text-muted-foreground">불러오는 중…</p>}>
        <AuthGuard>
          <main className="flex min-h-0 flex-1 flex-col">{children}</main>
          <BottomTabs />
        </AuthGuard>
      </Suspense>
    </div>
  );
}
