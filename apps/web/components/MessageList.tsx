"use client";

import { Fragment, useEffect, useRef } from "react";
import type { Message } from "@/lib/chat";
import { parseSuggestions, suggestionMessageIds } from "@/lib/suggestions";
import { cn } from "@/lib/utils";
import { PendingTask } from "./PendingTask";
import { SourceLinks } from "./SourceLinks";
import { SuggestionChips } from "./SuggestionChips";
import { TaskSteps } from "./TaskSteps";

type MessageListProps = {
  messages: Message[];
  // 서버 응답을 기다리는 동안 미리 보여 줄 내 메시지. ?는 없어도 된다는 뜻(Optional).
  pendingText?: string;
  // '예약 작업' 대화방이면 true(6단계). 자동으로 생긴 메시지(서버 origin이 schedule·watch)에 출처를 표시하고
  // 예약 작업 결과에 [제안] 버튼을 붙일 수 있다.
  automationRoom?: boolean;
  // [제안] 문장을 이 대화에 일반 메시지로 보낸다. 없으면 버튼을 그리지 않는다.
  onSuggestion?: (text: string) => Promise<unknown>;
  // 입력창에서 보내는 중이면 true([제안] 버튼도 막는다).
  sending?: boolean;
  // 내 메시지 없이 시작해 아직 답변이 없는 task(예약 작업 실행 중). 목록 끝에 진행 상황을 붙인다.
  openTaskIds?: number[];
};

type SuggestionProps = Pick<MessageListProps, "onSuggestion" | "sending">;

// 대화의 메시지 목록. 답변(assistant) 아래에는 그 답변을 만든 task의 단계를 붙인다.
export function MessageList(props: MessageListProps) {
  const { messages, pendingText, automationRoom = false, openTaskIds = [] } = props;
  const endRef = useRef<HTMLDivElement>(null);
  // 새 메시지가 오면 맨 아래로 스크롤한다(jsdom에는 scrollIntoView가 없어 ?.()). 의존성은 보이는 말풍선 수.
  const bubbleCount = messages.length + (pendingText ? 1 : 0) + openTaskIds.length;
  useEffect(() => {
    if (bubbleCount > 0) endRef.current?.scrollIntoView?.({ block: "end" });
  }, [bubbleCount]);
  if (bubbleCount === 0) {
    return <p className="m-auto text-sm text-muted-foreground">대화를 시작해 보세요.</p>;
  }
  // 답변이 이미 있는 task. 답변이 없는 내 메시지의 task는 답변 자리에 진행 상황(승인 카드 포함)을 그린다.
  const answeredTaskIds = new Set(
    messages.filter((message) => message.role === "assistant").map((message) => message.task_id),
  );
  const withChips = automationRoom ? suggestionMessageIds(messages) : new Set<number>();
  return (
    <div>
      <ul aria-label="메시지 목록" className="flex flex-col gap-3">
        {messages.map((message) => (
          <Fragment key={message.id}>
            <MessageBubble
              message={message}
              origin={automationRoom ? automaticOriginText(message) : null}
              suggestion={withChips.has(message.id) ? props : undefined}
            />
            {message.role === "user" &&
              message.task_id !== null &&
              !answeredTaskIds.has(message.task_id) && <PendingTaskItem taskId={message.task_id} />}
          </Fragment>
        ))}
        {openTaskIds.map((taskId) => (
          <PendingTaskItem key={`open-${taskId}`} taskId={taskId} label="예약 작업 실행 중" />
        ))}
        {pendingText && <PendingBubble text={pendingText} />}
      </ul>
      <div ref={endRef} />
    </div>
  );
}

// 자동으로 생긴 메시지의 출처 표시. 서버가 준 origin으로 정한다(채팅 메시지는 표시하지 않는다).
const AUTOMATIC_ORIGIN_TEXT: Partial<Record<Message["origin"], string>> = {
  schedule: "예약 작업 결과",
  watch: "조건 감시 알림",
};

function automaticOriginText(message: Message): string | null {
  if (message.role !== "assistant") return null;
  return AUTOMATIC_ORIGIN_TEXT[message.origin] ?? null;
}

type BubbleProps = {
  message: Message;
  origin: string | null;
  // 있으면 본문의 [제안] 줄을 버튼으로 붙인다.
  suggestion?: SuggestionProps;
};

function MessageBubble({ message, origin, suggestion }: BubbleProps) {
  const isUser = message.role === "user";
  return (
    <li className={cn("flex flex-col gap-1", isUser ? "items-end" : "items-start")}>
      <span className="text-xs text-muted-foreground">
        {isUser ? "나" : "마이에이전트"}
        {origin && ` · ${origin}`}
      </span>
      <div
        className={cn(
          "max-w-[85%] min-w-0 whitespace-pre-wrap break-words rounded-2xl px-3 py-2 text-base wrap-anywhere",
          isUser ? "bg-primary text-primary-foreground" : "bg-muted",
        )}
      >
        {message.content}
        {suggestion?.onSuggestion && (
          <SuggestionChips
            suggestions={parseSuggestions(message.content)}
            onSend={suggestion.onSuggestion}
            disabled={suggestion.sending}
          />
        )}
        {message.origin === "watch" && <WatchSources sources={message.sources} />}
        {!isUser && message.task_id !== null && <TaskSteps taskId={message.task_id} />}
      </div>
    </li>
  );
}

// 조건 감시 알림이 근거로 삼은 출처 링크(서버 messages.sources). 외부 서비스가 준 주소라 http·https만 링크로 만든다.
// 본문 안의 "출처:" 줄은 모델 출력이라 링크로 만들지 않고 글자 그대로 둔다.
function WatchSources({ sources }: { sources: readonly string[] }) {
  if (sources.length === 0) return null;
  return (
    <div className="mt-2 border-t pt-2 text-sm whitespace-normal">
      <SourceLinks sources={sources} />
    </div>
  );
}

// 답변 자리. 승인 카드가 360px 폭에서도 넉넉하도록 말풍선(85%)보다 넓게 쓴다.
function PendingTaskItem({ taskId, label }: { taskId: number; label?: string }) {
  return (
    <li className="flex flex-col items-start gap-1">
      <span className="text-xs text-muted-foreground">마이에이전트{label && ` · ${label}`}</span>
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
