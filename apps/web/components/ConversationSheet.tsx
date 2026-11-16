"use client";

import { CalendarClock, MessagesSquare, Plus } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetTrigger } from "@/components/ui/sheet";
import { isAutomationRoom } from "@/lib/automations";
import { useConversations, useCreateConversation } from "@/lib/chat";
import { cn } from "@/lib/utils";

// 왼쪽에서 열리는 대화 목록. 대화를 고르거나 새로 만든다.
export function ConversationSheet({ currentId }: { currentId: number }) {
  const [open, setOpen] = useState(false);
  const router = useRouter();
  const create = useCreateConversation();

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
      <SheetContent side="left" className="pt-[env(safe-area-inset-top)]">
        <SheetHeader>
          <SheetTitle>대화</SheetTitle>
        </SheetHeader>
        <div className="flex flex-col gap-2 overflow-y-auto px-4 pb-4">
          <Button onClick={handleCreate} disabled={create.isPending} className="h-11 text-base">
            <Plus aria-hidden="true" />새 대화
          </Button>
          <ConversationLinks currentId={currentId} onPick={() => setOpen(false)} />
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
