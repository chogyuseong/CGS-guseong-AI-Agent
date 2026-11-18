// /chat 진입 화면(app/(tabs)/chat/page.tsx). 화면을 그리지 않고 알맞은 대화(/chat/{id})로 넘긴다.
// - 쓰는 lib(lib/chat.ts): useConversations(GET /api/conversations), useCreateConversation(POST /api/conversations).
// - 대화를 지운 뒤에도 ConversationSheet가 여기로 보낸다(router.replace("/chat")). 지운 대화로 되돌아가지 않는 것은
//   lib/chat.ts useDeleteConversation이 캐시 목록에서 먼저 빼 두기 때문이다.
// 고칠 때 주의: router.replace를 쓰므로 뒤로 가기에 /chat이 남지 않는다(push로 바꾸면 뒤로 가기가 다시 여기로 와 튕긴다).
"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef } from "react";
import { useConversations, useCreateConversation } from "@/lib/chat";

// /chat에 들어오면 가장 최근 대화로 보낸다. 대화가 하나도 없으면 새로 만든 뒤 보낸다.
export function ChatHome() {
  const router = useRouter();
  const { data: conversations, isError } = useConversations();
  const { mutate: createConversation, isError: createFailed } = useCreateConversation();
  // useRef: 다시 그려도 유지되는 값. 개발 모드에서 effect가 두 번 실행돼도 대화를 두 개 만들지 않게 막는다.
  const createdRef = useRef(false);

  useEffect(() => {
    if (!conversations) return;
    const latest = conversations[0]; // 서버가 최근 활동순으로 돌려준다.
    if (latest) {
      router.replace(`/chat/${latest.id}`);
      return;
    }
    if (createdRef.current) return;
    createdRef.current = true;
    createConversation(undefined, {
      onSuccess: (conversation) => router.replace(`/chat/${conversation.id}`),
    });
  }, [conversations, createConversation, router]);

  const text = isError || createFailed ? "대화를 불러오지 못했습니다." : "대화를 여는 중…";
  return <p className="m-auto p-4 text-sm text-muted-foreground">{text}</p>;
}
