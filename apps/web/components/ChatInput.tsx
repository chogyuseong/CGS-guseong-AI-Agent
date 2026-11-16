"use client";

import { SendHorizontal } from "lucide-react";
import { type FormEvent, useState } from "react";
import { Button } from "@/components/ui/button";

type ChatInputProps = {
  // 보낼 내용을 받아 서버로 보내는 함수. Promise는 "나중에 끝나는 작업"이다(Python의 awaitable).
  // 실패하면 예외를 던지고, 그때는 입력한 내용을 지우지 않는다.
  onSend: (text: string) => Promise<unknown> | unknown;
  pending: boolean;
};

const MAX_LENGTH = 4000;

// 채팅 입력창. 글자 16px(text-base): 아이폰 사파리는 16px 미만 입력칸을 누르면 화면을 확대한다.
// 전송 중에는 입력과 버튼을 막아 같은 요청이 두 번 가지 않게 한다.
export function ChatInput({ onSend, pending }: ChatInputProps) {
  // useState: 컴포넌트가 기억하는 값과 그 값을 바꾸는 함수. (폼 객체의 cleaned_data를 화면이 직접 들고 있는 느낌)
  const [text, setText] = useState("");
  const trimmed = text.trim();

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!trimmed || pending) return;
    try {
      await onSend(trimmed);
      setText("");
    } catch {
      // 오류 문구는 부모(ChatScreen)가 보여 준다. 여기서는 입력 내용만 남겨 둔다.
    }
  }

  return (
    <form onSubmit={handleSubmit} className="border-t bg-background p-2">
      {pending && <PendingStatus />}
      <div className="flex items-end gap-2">
        <label htmlFor="chat-input" className="sr-only">
          메시지 입력
        </label>
        <textarea
          id="chat-input"
          rows={1}
          value={text}
          maxLength={MAX_LENGTH}
          disabled={pending}
          onChange={(event) => setText(event.target.value)}
          placeholder="무엇을 도와드릴까요?"
          className="block max-h-40 min-h-11 flex-1 resize-none rounded-lg border bg-background px-3 py-2.5 text-base outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-60"
        />
        <Button type="submit" aria-label="전송" disabled={pending || !trimmed} className="size-11">
          <SendHorizontal aria-hidden="true" className="size-5" />
        </Button>
      </div>
    </form>
  );
}

// 진행 중 표시. role="status"는 화면 낭독기에 바뀐 내용을 알려 주는 영역이라는 뜻이다.
function PendingStatus() {
  return (
    <p role="status" className="px-1 pb-1 text-xs text-muted-foreground">
      답변을 기다리는 중…
    </p>
  );
}
