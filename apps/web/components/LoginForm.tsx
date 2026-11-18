"use client";

import { useRouter } from "next/navigation";
import { type FormEvent, useEffect } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useLogin, useMe } from "@/lib/auth";

// 로그인 폼. 회원가입은 없다(계정은 서버의 create-user 명령으로만 만든다).
//
// 쓰는 곳: app/login/page.tsx(탭 밖이라 AuthGuard·하단 탭이 없다).
// 서버 호출: lib/auth.ts의 useLogin → POST /api/auth/login(성공하면 응답 사용자로 ["auth","me"] 캐시를 바로 채운다),
//   useMe → GET /api/auth/me(401이면 null). 세션은 서버가 주는 HttpOnly 쿠키라 화면은 토큰을 다루지 않는다.
//
// 고칠 때 주의:
// - 오류 문구는 lib/auth.ts의 login이 상태 코드로 고른다(401 이메일·비밀번호 불일치, 403 APP_ORIGIN 불일치).
//   서버 응답 글자를 그대로 보이지 않는다.
// - 로그인 뒤 가는 곳은 "/chat"이다. 바꾸면 e2e/login.spec.ts(로그인 → /chat/<번호>)도 고친다.
// - autoComplete(username·current-password)는 비밀번호 관리자가 칸을 알아보게 하는 값이라 지우지 않는다.
export function LoginForm() {
  useRedirectWhenLoggedIn();
  const loginMutation = useLogin();

  // FormEvent<HTMLFormElement>: "form에서 일어난 제출 이벤트"라는 타입. (Django 뷰가 받는 request에 해당)
  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); // 브라우저 기본 동작(페이지 새로고침 제출)을 막고 JS로 보낸다.
    const form = new FormData(event.currentTarget);
    loginMutation.mutate({
      email: String(form.get("email") ?? ""),
      password: String(form.get("password") ?? ""),
    });
  }

  return (
    <Card className="w-full max-w-sm">
      <CardHeader>
        <CardTitle className="text-xl">마이에이전트 로그인</CardTitle>
      </CardHeader>
      <CardContent>
        <form onSubmit={handleSubmit} className="flex flex-col gap-4">
          <LoginField name="email" label="이메일" type="email" autoComplete="username" />
          <LoginField
            name="password"
            label="비밀번호"
            type="password"
            autoComplete="current-password"
          />
          <LoginError message={loginMutation.error?.message} />
          <LoginSubmit pending={loginMutation.isPending} />
        </form>
      </CardContent>
    </Card>
  );
}

// 이미 로그인했다면(로그인 성공 직후 포함) 채팅으로 보낸다.
// 로그인 성공 직후에는 useLogin이 me 캐시를 채우므로 이 effect가 그 값으로 바로 이동한다(따로 이동 코드가 없다).
// replace라 뒤로 가기로 로그인 화면에 돌아오지 않는다.
// 직접 만든 훅: use로 시작하는 함수 안에서 다른 훅을 묶어 재사용한다. (Django의 믹스인처럼 동작을 묶어 붙이는 방식)
function useRedirectWhenLoggedIn() {
  const router = useRouter();
  const { data: user } = useMe();
  useEffect(() => {
    if (user) router.replace("/chat");
  }, [user, router]);
}

// 로그인 버튼. 높이 44px(h-11), 요청 중에는 눌리지 않게 막는다.
function LoginSubmit({ pending }: { pending: boolean }) {
  return (
    <Button type="submit" className="h-11 w-full text-base" disabled={pending}>
      {pending ? "로그인 중…" : "로그인"}
    </Button>
  );
}

// message?: string — 물음표는 "없을 수도 있음"(Optional[str])이라는 뜻이다.
function LoginError({ message }: { message?: string }) {
  if (!message) return null;
  return (
    <p role="alert" className="text-sm text-destructive">
      {message}
    </p>
  );
}

type LoginFieldProps = {
  name: string;
  label: string;
  type: "email" | "password";
  autoComplete: string;
};

// 입력칸 하나. 높이 44px(min-h-11), 글자 16px(text-base)로 모바일 기준을 맞춘다.
function LoginField({ name, label, type, autoComplete }: LoginFieldProps) {
  return (
    <label className="flex flex-col gap-1.5 text-sm font-medium">
      {label}
      <input
        name={name}
        type={type}
        autoComplete={autoComplete}
        required
        className="min-h-11 rounded-lg border bg-background px-3 text-base font-normal outline-none focus-visible:ring-2 focus-visible:ring-ring"
      />
    </label>
  );
}
