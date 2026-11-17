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
