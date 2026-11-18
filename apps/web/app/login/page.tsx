// 맡은 일: 로그인 화면(/login). 입력·제출·오류 문구는 components/LoginForm이 맡고 여기서는 가운데 배치만 한다.
// 들어오는 곳: AuthGuard가 로그인하지 않은 사용자를 이 주소로 보낸다(router.replace("/login")).
//   로그인에 성공하면(또는 이미 로그인 상태면) LoginForm이 /chat으로 보낸다.
// 고칠 때 주의: 경로를 바꾸면 components/AuthGuard.tsx의 이동 주소와 e2e(helpers.ts의 로그인 도우미,
//   dark-mode·widths spec의 page.goto("/login"), login.spec의 toHaveURL(/\/login$/))를 함께 고친다.

import { LoginForm } from "@/components/LoginForm";

// /login 화면. 탭 막대가 없는 단독 화면이라 (tabs) 묶음 밖에 둔다.
// min-h-dvh: 폰 주소창이 보였다 사라져도 실제로 보이는 높이 기준으로 세로 가운데에 둔다.
// 위·아래 패딩은 노치·홈 바 영역(safe-area)과 1rem 중 큰 값이다(layout.tsx의 viewportFit: "cover"가 있어야 값이 들어온다).
export default function LoginPage() {
  return (
    <main className="flex min-h-dvh flex-col items-center justify-center p-4 pt-[max(1rem,env(safe-area-inset-top))] pb-[max(1rem,env(safe-area-inset-bottom))]">
      <LoginForm />
    </main>
  );
}
