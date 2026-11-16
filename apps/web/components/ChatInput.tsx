"use client";

import { useState } from "react";

// 채팅 입력창. 0단계에서는 입력만 되고 전송은 하지 않는다(1단계에서 연결).
// 글자 크기 16px(text-base) 이상: 아이폰 사파리는 16px 미만 입력칸을 누르면 화면을 확대한다.
export function ChatInput() {
  // useState: 컴포넌트가 기억하는 값과 그 값을 바꾸는 함수. (폼 객체의 cleaned_data를 화면이 직접 들고 있는 느낌)
  const [text, setText] = useState("");
  return (
    <div className="border-t bg-background p-2">
      <label htmlFor="chat-input" className="sr-only">
        메시지 입력
      </label>
      <textarea
        id="chat-input"
        rows={1}
        value={text}
        onChange={(event) => setText(event.target.value)}
        placeholder="무엇을 도와드릴까요?"
        className="block min-h-11 w-full resize-none rounded-lg border bg-background px-3 py-2.5 text-base outline-none focus-visible:ring-2 focus-visible:ring-ring"
      />
    </div>
  );
}
