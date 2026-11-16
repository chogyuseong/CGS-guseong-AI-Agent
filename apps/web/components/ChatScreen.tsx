"use client";

import { isAutomationRoom } from "@/lib/automations";
import {
  openScheduleTaskIds,
  useConversations,
  useMessages,
  useSendMessage,
  useTasks,
} from "@/lib/chat";
import { ChatInput } from "./ChatInput";
import { ConversationSheet } from "./ConversationSheet";
import { MessageList } from "./MessageList";

// 대화 하나의 채팅 화면: 상단(대화 목록 버튼과 제목), 메시지 목록, 하단 입력창.
// (Django로 치면 conversation_detail 뷰 + 템플릿을 한 컴포넌트에 둔 것)
// '예약 작업' 대화방(6단계)이면 예약 결과의 [제안] 버튼과 진행 중인 예약 task를 함께 보여 준다.
export function ChatScreen({ conversationId }: { conversationId: number }) {
  const { data: conversations } = useConversations();
  const title = conversations?.find((item) => item.id === conversationId)?.title;
  const automationRoom = isAutomationRoom(title);
  const send = useSendMessage(conversationId);

  return (
    <>
      <ChatHeader conversationId={conversationId} title={title ?? "대화"} />
      <section className="flex min-h-0 flex-1 flex-col overflow-y-auto p-4">
        <h1 className="sr-only">채팅</h1>
        {automationRoom && <AutomationRoomNotice />}
        <ChatMessages conversationId={conversationId} automationRoom={automationRoom} send={send} />
      </section>
      {send.error && (
        <p role="alert" className="border-t px-4 py-2 text-sm text-destructive">
          {send.error.message}
        </p>
      )}
      {/* mutateAsync: 전송이 끝날 때까지 기다릴 수 있는 함수. 실패하면 예외를 던져 입력 내용을 남긴다. */}
      <ChatInput onSend={send.mutateAsync} pending={send.isPending} />
    </>
  );
}

type ChatMessagesProps = {
  conversationId: number;
  automationRoom: boolean;
  send: ReturnType<typeof useSendMessage>;
};

function ChatMessages({ conversationId, automationRoom, send }: ChatMessagesProps) {
  const messages = useMessages(conversationId);
  // 예약 task는 내 메시지 없이 시작하므로 작업 목록에서 진행 중인 것을 찾는다(이 대화방에서만 가져온다).
  const tasks = useTasks({ enabled: automationRoom });
  // 서버가 답할 때까지(1단계는 몇 초) 내가 보낸 문장을 "보내는 중"으로 먼저 보여 준다.
  const pendingText = send.isPending ? send.variables : undefined;
  if (messages.isPending)
    return <p className="m-auto text-sm text-muted-foreground">불러오는 중…</p>;
  if (messages.isError) {
    return <p className="m-auto text-sm text-destructive">메시지를 불러오지 못했습니다.</p>;
  }
  return (
    <MessageList
      messages={messages.data}
      pendingText={pendingText}
      automationRoom={automationRoom}
      // [제안] 버튼은 '예약 작업' 대화방에서만. 누르면 입력창 전송과 같은 길(일반 채팅 메시지)로 보낸다.
      onSuggestion={automationRoom ? send.mutateAsync : undefined}
      sending={send.isPending}
      openTaskIds={
        automationRoom ? openScheduleTaskIds(tasks.data, conversationId, messages.data) : []
      }
    />
  );
}

function AutomationRoomNotice() {
  return (
    <p className="mb-3 rounded-lg border bg-muted/40 p-2 text-xs text-muted-foreground">
      예약 작업 결과와 조건 감시 알림이 이 대화방에 남습니다. 자동 작업은 조회만 하고, [제안] 버튼은
      누를 때만 그 문장을 보냅니다.
    </p>
  );
}

function ChatHeader({ conversationId, title }: { conversationId: number; title: string }) {
  return (
    <header className="flex h-12 shrink-0 items-center gap-1 border-b px-1">
      <ConversationSheet currentId={conversationId} />
      <p className="truncate text-base font-semibold">{title}</p>
    </header>
  );
}
