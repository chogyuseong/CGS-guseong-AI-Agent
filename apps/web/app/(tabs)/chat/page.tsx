import { ChatInput } from "@/components/ChatInput";

// 채팅 화면(0단계): 비어 있는 메시지 영역과 하단 입력창만 있다.
// page.tsx 파일 하나가 주소 하나다. 이 파일은 /chat. (Django의 urls.py + 뷰를 파일 위치로 정하는 방식)
export default function ChatPage() {
  return (
    <>
      <section aria-label="메시지 목록" className="flex flex-1 flex-col overflow-y-auto p-4">
        <h1 className="sr-only">채팅</h1>
        <p className="m-auto text-sm text-muted-foreground">대화를 시작해 보세요.</p>
      </section>
      <ChatInput />
    </>
  );
}
