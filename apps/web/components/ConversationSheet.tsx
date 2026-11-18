// 채팅 머리줄의 대화 목록 시트(왼쪽에서 열림): 대화 고르기, 새 대화, 지금 대화 지우기.
// - 쓰는 곳: ChatScreen의 ChatHeader.
// - 쓰는 lib(lib/chat.ts): useConversations(GET /api/conversations), useCreateConversation(POST /api/conversations,
//   제목은 보내지 않는다), useDeleteConversation(DELETE /api/conversations/{conversation_id}).
// 고칠 때 주의:
// - e2e(conversation-delete.spec.ts, dark-mode.spec.ts, widths.spec.ts)가 버튼 "대화 목록"·"새 대화",
//   nav "대화 목록"과 그 안의 링크 수, a[href="/chat/{id}"]를 찾는다.
// - 기본 닫기 버튼을 끄고 44px 닫기 버튼을 따로 둔다(BUILD_SPEC 10.1 터치 영역 44px).
"use client";

import { CalendarClock, MessagesSquare, Plus, X } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetClose,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";
import { isAutomationRoom } from "@/lib/automations";
import { useConversations, useCreateConversation, useDeleteConversation } from "@/lib/chat";
import { cn } from "@/lib/utils";
import { DeleteConversation } from "./DeleteConversation";

// 왼쪽에서 열리는 대화 목록. 대화를 고르거나 새로 만든다.
// currentId: 지금 보고 있는 대화. 목록에서 강조하고, "이 대화 지우기"의 대상이다.
// 부수 효과: 새 대화를 만들면 시트를 닫고 그 대화로 push, 지우면 시트를 닫고 /chat으로 replace(ChatHome이
// 남은 최근 대화로 다시 보낸다). 삭제 실패 문장은 DeleteConversation이 보여 주도록 mutateAsync의 예외를 그대로 둔다.
export function ConversationSheet({ currentId }: { currentId: number }) {
  const [open, setOpen] = useState(false);
  const router = useRouter();
  const create = useCreateConversation();
  const remove = useDeleteConversation();

  function handleCreate() {
    create.mutate(undefined, {
      onSuccess: (conversation) => {
        setOpen(false);
        router.push(`/chat/${conversation.id}`);
      },
    });
  }

  return (
    <Sheet open={open} onOpenChange={setOpen}>
      {/* render: Base UI 방식으로 "트리거를 이 Button으로 그려라"는 뜻. (위젯의 template_name을 바꾸는 것과 비슷) */}
      <SheetTrigger render={<Button variant="ghost" className="size-11" aria-label="대화 목록" />}>
        <MessagesSquare aria-hidden="true" className="size-5" />
      </SheetTrigger>
      {/* 기본 닫기 버튼(28px)은 손가락으로 누르기 작아서 끄고, 머리줄에 44px 닫기 버튼을 둔다(9단계 화면 점검). */}
      <SheetContent side="left" showCloseButton={false} className="pt-[env(safe-area-inset-top)]">
        <SheetHeader className="flex-row items-center justify-between py-2 pr-2">
          <SheetTitle>대화</SheetTitle>
          <SheetClose render={<Button variant="ghost" className="size-11" aria-label="닫기" />}>
            <X aria-hidden="true" className="size-5" />
          </SheetClose>
        </SheetHeader>
        <div className="flex flex-col gap-2 overflow-y-auto px-4 pb-4">
          <Button onClick={handleCreate} disabled={create.isPending} className="h-11 text-base">
            <Plus aria-hidden="true" />새 대화
          </Button>
          <ConversationLinks currentId={currentId} onPick={() => setOpen(false)} />
          <DeleteConversation
            onDelete={async () => {
              await remove.mutateAsync(currentId);
              setOpen(false);
              router.replace("/chat");
            }}
          />
        </div>
      </SheetContent>
    </Sheet>
  );
}

type ConversationLinksProps = {
  currentId: number;
  // 링크를 누르면 부르는 함수(시트 닫기). () => void는 "인자 없고 돌려주는 값 없는 함수" 타입.
  onPick: () => void;
};

// 대화 링크 목록. 순서는 서버가 준 그대로(최근 활동순)다. 지금 대화에는 aria-current="page"를 붙인다.
function ConversationLinks({ currentId, onPick }: ConversationLinksProps) {
  const { data: conversations = [] } = useConversations();
  return (
    <nav aria-label="대화 목록">
      <ul className="flex flex-col">
        {conversations.map((conversation) => (
          <li key={conversation.id}>
            <Link
              href={`/chat/${conversation.id}`}
              onClick={onPick}
              aria-current={conversation.id === currentId ? "page" : undefined}
              className={cn(
                "flex min-h-11 items-center rounded-md px-3 text-base",
                conversation.id === currentId && "bg-muted font-semibold",
              )}
            >
              {isAutomationRoom(conversation.title) ? (
                <AutomationRoomLabel title={conversation.title} />
              ) : (
                <span className="truncate">{conversation.title}</span>
              )}
            </Link>
          </li>
        ))}
      </ul>
    </nav>
  );
}

// '예약 작업' 대화방(6단계)은 사용자가 만든 대화가 아니라 자동 결과가 쌓이는 곳이라 아이콘과 한 줄 설명을 붙인다.
function AutomationRoomLabel({ title }: { title: string }) {
  return (
    <span className="flex min-w-0 items-center gap-2 py-1">
      <CalendarClock aria-hidden="true" className="size-4 shrink-0" />
      <span className="flex min-w-0 flex-col">
        <span className="truncate">{title}</span>
        <span className="truncate text-xs font-normal text-muted-foreground">
          자동 실행 결과·감시 알림
        </span>
      </span>
    </span>
  );
}
