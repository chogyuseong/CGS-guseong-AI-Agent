"use client";

import { useEffect, useRef } from "react";
import type { Message } from "@/lib/chat";
import { cn } from "@/lib/utils";
import { TaskSteps } from "./TaskSteps";

type MessageListProps = {
  messages: Message[];
  // 서버 응답을 기다리는 동안 미리 보여 줄 내 메시지. ?는 없어도 된다는 뜻(Optional).
  pendingText?: string;
};

// 대화의 메시지 목록. 답변(assistant) 아래에는 그 답변을 만든 task의 단계를 붙인다.
export function MessageList({ messages, pendingText }: MessageListProps) {
  const endRef = useRef<HTMLDivElement>(null);
  // 새 메시지가 오면 맨 아래로 스크롤한다. (jsdom 테스트에는 scrollIntoView가 없어서 ?.()로 부른다)
  // 두 번째 인자(의존성 배열)의 값이 바뀔 때만 effect가 다시 실행된다. 여기서는 보이는 말풍선 수.
  const bubbleCount = messages.length + (pendingText ? 1 : 0);
  useEffect(() => {
    if (bubbleCount > 0) endRef.current?.scrollIntoView?.({ block: "end" });
  }, [bubbleCount]);

  if (messages.length === 0 && !pendingText) {
    return <p className="m-auto text-sm text-muted-foreground">대화를 시작해 보세요.</p>;
  }
  return (
    <div>
      <ul aria-label="메시지 목록" className="flex flex-col gap-3">
        {messages.map((message) => (
          <MessageBubble key={message.id} message={message} />
        ))}
        {pendingText && <PendingBubble text={pendingText} />}
      </ul>
      <div ref={endRef} />
    </div>
  );
}

function MessageBubble({ message }: { message: Message }) {
  const isUser = message.role === "user";
  return (
    <li className={cn("flex flex-col gap-1", isUser ? "items-end" : "items-start")}>
      <span className="text-xs text-muted-foreground">{isUser ? "나" : "마이에이전트"}</span>
      <div
        className={cn(
          "max-w-[85%] whitespace-pre-wrap break-words rounded-2xl px-3 py-2 text-base",
          isUser ? "bg-primary text-primary-foreground" : "bg-muted",
        )}
      >
        {message.content}
        {!isUser && message.task_id !== null && <TaskSteps taskId={message.task_id} />}
      </div>
    </li>
  );
}

function PendingBubble({ text }: { text: string }) {
  return (
    <li className="flex flex-col items-end gap-1 opacity-70">
      <span className="text-xs text-muted-foreground">나 · 보내는 중</span>
      <div className="max-w-[85%] whitespace-pre-wrap break-words rounded-2xl bg-primary px-3 py-2 text-base text-primary-foreground">
        {text}
      </div>
    </li>
  );
}
