"use client";

import { useRouter } from "next/navigation";
import { type ReactNode, useEffect } from "react";
import { useMe } from "@/lib/auth";

// 로그인한 사람에게만 children을 보여 준다. (Django의 @login_required)
// 서버 세션 쿠키는 HttpOnly라 JS가 읽을 수 없으므로 /api/auth/me로 물어본다.
//
// 쓰는 곳: app/(tabs)/layout.tsx(탭 화면 전체를 감싸고, 하단 탭도 이 안에 있다). 로그인 화면(app/login)은 밖이다.
// 서버 호출: lib/auth.ts의 useMe(GET /api/auth/me, retry 없음. 401 → null, 그 밖의 실패 → 오류).
//
// 고칠 때 주의:
// - 이 확인은 화면 표시용이다. 실제 보호는 서버 API가 요청마다 세션을 확인하는 것이다(화면을 우회해도 데이터는 못 받는다).
// - 로그인 안 됨(null)과 서버 오류(isError)를 구분한다. 서버가 잠깐 죽었을 때 로그인 화면으로 튕기지 않게 하려는 것이다.
// - 테스트: e2e/login.spec.ts(로그인 전 /chat → /login).
export function AuthGuard({ children }: { children: ReactNode }) {
  const router = useRouter();
  const { data: user, isPending, isError } = useMe();

  // useEffect: 화면을 그린 뒤에 실행할 일(여기서는 페이지 이동)을 둔다.
  // (그리는 도중에 이동하면 React가 경고하므로, 그린 다음 처리하는 자리에 둔다)
  useEffect(() => {
    if (!isPending && user === null) router.replace("/login");
  }, [isPending, user, router]);

  if (isError)
    return <CenteredMessage text="서버에 연결하지 못했습니다. 잠시 후 다시 열어 주세요." />;
  if (isPending || !user) return <CenteredMessage text="확인 중…" />;
  return <>{children}</>;
}

function CenteredMessage({ text }: { text: string }) {
  return (
    <p role="status" className="flex flex-1 items-center justify-center p-4 text-muted-foreground">
      {text}
    </p>
  );
}
