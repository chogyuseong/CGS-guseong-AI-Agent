// 대화 하나의 채팅 화면(app/(tabs)/chat/[id]/page.tsx가 그린다).
// - 쓰는 lib(lib/chat.ts): useConversations(GET /api/conversations, 제목), useMessages(GET .../{id}/messages),
//   useSendMessage(POST .../{id}/messages, 202 {task_id}), useTasks(GET /api/tasks, '예약 작업' 대화방에서만).
// - 근거: DECISIONS 2026-11-17 화면 절('예약 작업' 대화방, 진행 중 예약 task), 2026-11-17 제안 버튼은 입력창 채우기.
// 고칠 때 주의:
// - '예약 작업' 대화방은 제목 글자로 알아본다(lib/automations.ts isAutomationRoom ↔ 서버 rules.AUTOMATION_CONVERSATION_TITLE).
// - 전송 오류 줄(role=alert)은 useSendMessage가 던진 한국어 문장을 그대로 보인다. 입력 내용은 ChatInput이 남긴다.
"use client";

import { useRef, useState } from "react";
import { isAutomationRoom } from "@/lib/automations";
import {
  countUserMessages,
  openScheduleTaskIds,
  pendingBubbleText,
  useConversations,
  useMessages,
  useSendMessage,
  useTasks,
} from "@/lib/chat";
import { ChatInput, type Prefill } from "./ChatInput";
import { ConversationSheet } from "./ConversationSheet";
import { MessageList } from "./MessageList";

// 대화 하나의 채팅 화면: 상단(대화 목록 버튼과 제목), 메시지 목록, 하단 입력창.
// (Django로 치면 conversation_detail 뷰 + 템플릿을 한 컴포넌트에 둔 것)
// '예약 작업' 대화방(6단계)이면 예약 결과의 제안 버튼과 진행 중인 예약 task를 함께 보여 준다.
export function ChatScreen({ conversationId }: { conversationId: number }) {
  const { data: conversations } = useConversations();
  const title = conversations?.find((item) => item.id === conversationId)?.title;
  const automationRoom = isAutomationRoom(title);
  const send = useSendMessage(conversationId);
  // 6단계 보강: 제안 버튼은 입력창에 문장만 채운다(보내기는 사용자). 같은 문장을 다시 골라도 채우도록 id를 올린다.
  const [prefill, setPrefill] = useState<Prefill | null>(null);
  const pick = (text: string) => setPrefill((previous) => ({ id: (previous?.id ?? 0) + 1, text }));

  return (
    <>
      <ChatHeader conversationId={conversationId} title={title ?? "대화"} />
      <section className="flex min-h-0 flex-1 flex-col overflow-y-auto p-4">
        <h1 className="sr-only">채팅</h1>
        {automationRoom && <AutomationRoomNotice />}
        <ChatMessages
          conversationId={conversationId}
          automationRoom={automationRoom}
          send={send}
          onPick={pick}
        />
      </section>
      {send.error && (
        <p role="alert" className="border-t px-4 py-2 text-sm text-destructive">
          {send.error.message}
        </p>
      )}
      {/* mutateAsync: 전송이 끝날 때까지 기다릴 수 있는 함수. 실패하면 예외를 던져 입력 내용을 남긴다. */}
      <ChatInput onSend={send.mutateAsync} pending={send.isPending} prefill={prefill} />
    </>
  );
}

type ChatMessagesProps = {
  conversationId: number;
  automationRoom: boolean;
  send: ReturnType<typeof useSendMessage>;
  onPick: (text: string) => void;
};

// 메시지 목록 부분. 불러오는 중·실패를 여기서 처리하고, "보내는 중" 말풍선과 진행 중 예약 task를 MessageList에 넘긴다.
function ChatMessages({ conversationId, automationRoom, send, onPick }: ChatMessagesProps) {
  const messages = useMessages(conversationId);
  // 예약 task는 내 메시지 없이 시작하므로 작업 목록에서 진행 중인 것을 찾는다(이 대화방에서만 가져온다).
  const tasks = useTasks({ enabled: automationRoom });
  // 보내기 시작한 순간의 내 메시지 수. 다시 받은 목록에서 이 수가 늘면 "보내는 중" 말풍선을 숨긴다.
  // (렌더 중에 ref를 바꾸지만 처음 한 번만 정하고 같은 값을 유지하므로 여러 번 렌더돼도 결과가 같다)
  const sentFrom = useRef<number | undefined>(undefined);
  if (!send.isPending) sentFrom.current = undefined;
  else sentFrom.current ??= countUserMessages(messages.data ?? []);
  if (messages.isPending)
    return <p className="m-auto text-sm text-muted-foreground">불러오는 중…</p>;
  if (messages.isError) {
    return <p className="m-auto text-sm text-destructive">메시지를 불러오지 못했습니다.</p>;
  }
  // 서버가 답할 때까지 내가 보낸 문장을 "보내는 중"으로 먼저 보여 준다. 다시 받은 목록에 들어오면 숨긴다.
  const pendingText = pendingBubbleText(
    send.isPending,
    send.variables,
    sentFrom.current,
    messages.data,
  );
  return (
    <MessageList
      messages={messages.data}
      pendingText={pendingText}
      automationRoom={automationRoom}
      // 제안 버튼은 '예약 작업' 대화방에서만. 누르면 입력창에 문장을 채운다(보내지 않는다).
      onSuggestion={automationRoom ? onPick : undefined}
      sending={send.isPending}
      openTaskIds={
        automationRoom ? openScheduleTaskIds(tasks.data, conversationId, messages.data) : []
      }
    />
  );
}

// '예약 작업' 대화방 위의 안내. 자동 작업은 READ만 하고(절대 원칙 7) 제안은 보내지 않고 채우기만 한다는 것을 늘 보인다.
function AutomationRoomNotice() {
  return (
    <p className="mb-3 rounded-lg border bg-muted/40 p-2 text-xs text-muted-foreground">
      예약 작업 결과와 조건 감시 알림이 이 대화방에 남습니다. 자동 작업은 조회만 하고, 제안 버튼은
      누르면 입력창에 문장을 채웁니다. 보내기는 직접 합니다.
    </p>
  );
}

// 상단 머리줄: 대화 목록 시트 버튼(새 대화·대화 삭제 포함)과 제목. 제목은 목록을 받기 전엔 "대화"다.
function ChatHeader({ conversationId, title }: { conversationId: number; title: string }) {
  return (
    <header className="flex h-12 shrink-0 items-center gap-1 border-b px-1">
      <ConversationSheet currentId={conversationId} />
      <p className="truncate text-base font-semibold">{title}</p>
    </header>
  );
}
