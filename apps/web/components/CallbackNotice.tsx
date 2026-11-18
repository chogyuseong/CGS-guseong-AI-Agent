"use client";

import { CircleAlert, CircleCheck } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import { CONNECTIONS_PATH, callbackNotice, hasCallbackParams } from "@/lib/connections";
import { cn } from "@/lib/utils";

// Google 동의 화면에서 돌아왔을 때(/connections?google=...)의 안내.
// 처음 그릴 때 쿼리를 읽어 안내를 state에 담고, 주소에서는 쿼리를 지운다(새로 고침·뒤로 가기 때 다시 뜨지 않게).
// 안내 문장은 아는 코드만 고정 문구로 바꾼 것이다. 주소의 글자를 그대로 보여 주지 않는다.
// 막는 사고: 누군가 "?google=error&reason=<거짓 안내>" 링크를 보내 이 앱 화면에 원하는 문장을 띄우는 일.
//
// 쓰는 곳: app/(tabs)/connections/page.tsx(useSearchParams를 쓰므로 그 페이지가 Suspense로 감싼다).
// 서버 쪽: apps/api/app/routers/connections.py의 GET /api/connections/google/callback이 처리 뒤
//   /connections?google=connected[&added=mail] 또는 ?google=error&reason=<코드>로 돌려보낸다.
// 근거: DECISIONS 2026-11-17 "콜백 결과는 처음 그릴 때 한 번 읽어 안내를 state에 담고 쿼리를 지운다".
//
// 고칠 때 주의:
// - 쿼리 이름(google, reason, added)과 reason 코드 목록은 서버 콜백과 글자로 맞물려 있다. 문구 표는
//   lib/connections.ts의 CALLBACK_REASONS, 읽고 지우는 판단은 callbackNotice·hasCallbackParams다.
//   서버에 코드를 더하면 그 표에 문구를 더한다(없으면 일반 실패 문구로 보인다).
// - 테스트: components/CallbackNotice.test.tsx, e2e/connections.spec.ts(HTML 조각을 넣은 reason이 글자로 안 보이는지).
export function CallbackNotice() {
  const params = useSearchParams();
  const router = useRouter();
  // 첫 렌더에서 한 번만 읽는다. 아래 router.replace로 쿼리가 사라져도 안내는 state에 남는다.
  const [notice] = useState(() => callbackNotice(params));
  const shouldClean = hasCallbackParams(params);

  useEffect(() => {
    if (shouldClean) router.replace(CONNECTIONS_PATH, { scroll: false });
  }, [shouldClean, router]);

  if (notice === null) return null;
  const success = notice.tone === "success";
  const Icon = success ? CircleCheck : CircleAlert;
  return (
    <p
      role={success ? "status" : "alert"}
      className={cn(
        "flex items-start gap-2 rounded-lg border p-3 text-sm break-words",
        success
          ? "border-success/30 bg-success/5 text-success"
          : "border-destructive/40 bg-destructive/5 text-destructive",
      )}
    >
      <Icon aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
      <span className="min-w-0">{notice.text}</span>
    </p>
  );
}
