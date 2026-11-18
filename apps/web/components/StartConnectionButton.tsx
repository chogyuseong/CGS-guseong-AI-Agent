"use client";

import { LoaderCircle } from "lucide-react";
import { useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import type { StartScope } from "@/lib/connections";

// 4단계 보강: Google 연결 시작 버튼. 예전에는 링크 이동(GET)이었지만, 시작은 서버에 state를 만드는 일이라 POST로 부르고
// 서버가 준 Google 동의 화면 주소로 이동한다(lib/connections.ts의 startGoogleConnection).
// 누르는 즉시 state와 ref 두 겹으로 잠가 한 번만 보낸다. 이동하면 이 화면을 떠나므로 성공 뒤에는 잠금을 풀지 않는다.
//
// 쓰는 곳: GoogleConnection.tsx([Google 계정 연결]·[다시 연결], scope 없음 = 캘린더만),
//   MailPermission.tsx([메일 읽기 권한 추가], scope "mail" = 캘린더 + gmail.readonly).
// 서버 호출: POST /api/connections/google/start, 본문 {scope?: "mail"}, 응답 {authorization_url}.
// 근거: BUILD_SPEC 9.3("OAuth 시작은 상태를 만들므로 POST"), DECISIONS 2026-11-17 Google 연결 시작 POST 전환.
//   POST라 서버의 Origin 검사를 거친다(예전 GET + <a href> 링크 이동 결정을 대체).
//
// 고칠 때 주의:
// - 시작 실패 문구(401, 409 not_configured·mail_disabled)는 lib/connections.ts의 startErrorText가 고른다.
// - 테스트: components/GoogleConnection.test.tsx(가짜 onStart), e2e/connections.spec.ts(가짜 서버가 앱 안 주소를 준다).

// 연결 시작 함수 모양. 실제 구현은 lib/connections.ts의 startGoogleConnection이고, 실패하면 한국어 Error를 던진다.
export type StartConnection = (scope: StartScope) => Promise<void>;

type Props = {
  label: string;
  // undefined면 기본 연결(캘린더만), "mail"이면 메일 읽기 권한까지 요청한다.
  scope: StartScope;
  // 연결할 수 없는 상태면 false로 버튼을 막는다. 기본 연결은 connection.available(서버 Google 설정),
  // 메일 권한 추가는 거기에 mail_enabled(메일 암호화 키)까지 본다(MailPermission.tsx).
  available: boolean;
  onStart: StartConnection;
  // 그 상태에서 사용자가 해야 할 주된 행동이면 true(채운 버튼).
  primary?: boolean;
};

const BIG_BUTTON = "h-12 w-full min-w-0 text-base";
const START_FALLBACK = "연결을 시작하지 못했습니다. 잠시 후 다시 시도해 주세요.";

export function StartConnectionButton({
  label,
  scope,
  available,
  onStart,
  primary = false,
}: Props) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const locked = useRef(false);

  // 성공하면 onStart가 Google 동의 화면으로 페이지를 옮기므로 잠금을 그대로 둔다(이동 직전 두 번째 POST 방지).
  // 실패했을 때만 잠금을 풀어 다시 누를 수 있게 한다.
  async function start() {
    if (locked.current) return;
    locked.current = true;
    setBusy(true);
    setError(null);
    try {
      await onStart(scope);
    } catch (caught) {
      locked.current = false;
      setBusy(false);
      setError(caught instanceof Error ? caught.message : START_FALLBACK);
    }
  }

  return (
    <div className="flex flex-col gap-2">
      <Button
        type="button"
        variant={primary ? "default" : "outline"}
        disabled={!available || busy}
        onClick={start}
        className={BIG_BUTTON}
      >
        {label}
      </Button>
      {busy && (
        <p role="status" className="flex items-center gap-2 text-sm text-muted-foreground">
          <LoaderCircle aria-hidden="true" className="size-4 shrink-0 animate-spin" />
          Google 동의 화면으로 이동하는 중…
        </p>
      )}
      {error && (
        <p role="alert" className="break-words text-sm text-destructive">
          {error}
        </p>
      )}
    </div>
  );
}
