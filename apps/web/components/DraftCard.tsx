// 메시지 초안 카드(draft_message 도구의 결과).
// - 쓰는 곳: TaskSteps(답변 아래, 작업 기록 탭). 완료(done)된 단계의 출력을 lib/stepOutput.ts readMessageDraft가
//   {recipient, body}로 읽었을 때만 그린다(서버 data.kind="message_draft", sent=false인 경우만).
// - 근거: BUILD_SPEC 7.4 초안, 절대 원칙 2(메시지 발송 금지), DECISIONS 2026-11-17 draft_message.
// 고칠 때 주의: e2e dark-mode.spec.ts가 article "메시지 초안"을 찾는다. DraftCard.test.tsx도 같이 본다.
"use client";

import { Check, Copy, MessageSquareText } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import type { MessageDraft } from "@/lib/stepOutput";

// 4단계 보강: 메시지 초안 카드(BUILD_SPEC 7.4). 받는 사람과 본문을 나눠 보여 주고 복사 버튼을 둔다.
// 이 앱은 메시지를 보내지 않는다. "보냈다"는 문구를 쓰지 않고 직접 보내라고 안내한다.
// 본문은 텍스트로만 그린다(HTML로 해석하지 않음). 줄바꿈은 그대로 보인다.
const NOT_SENT_TEXT = "보내지 않았습니다. 확인한 뒤 복사해서 직접 보내 주세요.";
const COPY_FAILED_TEXT = "복사하지 못했습니다. 본문을 길게 눌러 직접 복사해 주세요.";

// 막는 사고: 앱이 대신 보낸 것처럼 보이는 것. 성공한 복사 뒤에도 "보내지 않았습니다" 안내는 그대로 남는다.
// 클립보드는 https·사용자 동작 안에서만 되는 브라우저가 있어 실패하면 길게 눌러 복사하라고 안내한다(role=alert).
export function DraftCard({ draft }: { draft: MessageDraft }) {
  const [copy, setCopy] = useState<"idle" | "done" | "failed">("idle");

  async function copyBody() {
    try {
      await navigator.clipboard.writeText(draft.body);
      setCopy("done");
    } catch {
      setCopy("failed");
    }
  }

  return (
    <article
      aria-label="메시지 초안"
      className="mt-2 flex min-w-0 flex-col gap-2 rounded-xl border bg-card p-3 text-card-foreground"
    >
      <div className="flex items-center gap-2 text-sm font-semibold">
        <MessageSquareText aria-hidden="true" className="size-4 shrink-0" />
        메시지 초안
      </div>
      <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-2 text-sm">
        <dt className="text-muted-foreground">받는 사람</dt>
        <dd className="font-medium wrap-anywhere">{draft.recipient}</dd>
      </dl>
      <p className="rounded-md border bg-muted/40 p-2 text-sm whitespace-pre-wrap wrap-anywhere">
        {draft.body}
      </p>
      <Button
        type="button"
        variant="outline"
        onClick={copyBody}
        className="h-11 w-full min-w-0 text-base"
      >
        {copy === "done" ? <Check aria-hidden="true" /> : <Copy aria-hidden="true" />}
        {copy === "done" ? "복사했습니다" : "본문 복사"}
      </Button>
      <p role={copy === "failed" ? "alert" : undefined} className="text-xs text-muted-foreground">
        {copy === "failed" ? COPY_FAILED_TEXT : NOT_SENT_TEXT}
      </p>
    </article>
  );
}
