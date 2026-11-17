"use client";

import { CircleAlert, CircleCheck } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import { CONNECTIONS_PATH, callbackNotice, hasCallbackParams } from "@/lib/connections";
import { cn } from "@/lib/utils";

// Google 동의 화면에서 돌아왔을 때(/connections?google=...)의 안내.
// 처음 그릴 때 쿼리를 읽어 안내를 state에 담고, 주소에서는 쿼리를 지운다(새로 고침·뒤로 가기 때 다시 뜨지 않게).
// 안내 문장은 아는 코드만 고정 문구로 바꾼 것이다. 주소의 글자를 그대로 보여 주지 않는다.
export function CallbackNotice() {
  const params = useSearchParams();
  const router = useRouter();
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
