"use client";

import { Fragment, useEffect, useRef } from "react";
import type { Message } from "@/lib/chat";
import { cn } from "@/lib/utils";
import { PendingTask } from "./PendingTask";
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
  // 답변이 이미 있는 task. 답변이 없는 내 메시지의 task는 답변 자리에 진행 상황(승인 카드 포함)을 그린다.
  const answeredTaskIds = new Set(
    messages.filter((message) => message.role === "assistant").map((message) => message.task_id),
  );
  return (
    <div>
      <ul aria-label="메시지 목록" className="flex flex-col gap-3">
        {messages.map((message) => (
          <Fragment key={message.id}>
            <MessageBubble message={message} />
            {message.role === "user" &&
              message.task_id !== null &&
              !answeredTaskIds.has(message.task_id) && <PendingTaskItem taskId={message.task_id} />}
          </Fragment>
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

// 답변 자리. 승인 카드가 360px 폭에서도 넉넉하도록 말풍선(85%)보다 넓게 쓴다.
function PendingTaskItem({ taskId }: { taskId: number }) {
  return (
    <li className="flex flex-col items-start gap-1">
      <span className="text-xs text-muted-foreground">마이에이전트</span>
      <div className="flex w-full flex-col gap-1 rounded-2xl bg-muted px-3 py-2">
        <PendingTask taskId={taskId} />
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
