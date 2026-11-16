"use client";

import { useConversations, useMessages, useSendMessage } from "@/lib/chat";
import { ChatInput } from "./ChatInput";
import { ConversationSheet } from "./ConversationSheet";
import { MessageList } from "./MessageList";

// 대화 하나의 채팅 화면: 상단(대화 목록 버튼과 제목), 메시지 목록, 하단 입력창.
// (Django로 치면 conversation_detail 뷰 + 템플릿을 한 컴포넌트에 둔 것)
export function ChatScreen({ conversationId }: { conversationId: number }) {
  const messages = useMessages(conversationId);
  const send = useSendMessage(conversationId);
  // 서버가 답할 때까지(1단계는 몇 초) 내가 보낸 문장을 "보내는 중"으로 먼저 보여 준다.
  const pendingText = send.isPending ? send.variables : undefined;

  return (
    <>
      <ChatHeader conversationId={conversationId} />
      <section className="flex min-h-0 flex-1 flex-col overflow-y-auto p-4">
        <h1 className="sr-only">채팅</h1>
        {messages.isPending && <p className="m-auto text-sm text-muted-foreground">불러오는 중…</p>}
        {messages.isError && (
          <p className="m-auto text-sm text-destructive">메시지를 불러오지 못했습니다.</p>
        )}
        {messages.data && <MessageList messages={messages.data} pendingText={pendingText} />}
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

function ChatHeader({ conversationId }: { conversationId: number }) {
  const { data: conversations } = useConversations();
  const title = conversations?.find((item) => item.id === conversationId)?.title ?? "대화";
  return (
    <header className="flex h-12 shrink-0 items-center gap-1 border-b px-1">
      <ConversationSheet currentId={conversationId} />
      <p className="truncate text-base font-semibold">{title}</p>
    </header>
  );
}
