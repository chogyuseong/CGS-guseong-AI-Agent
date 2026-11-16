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
