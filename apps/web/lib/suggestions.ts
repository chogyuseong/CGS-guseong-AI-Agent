// 예약 작업 답변의 제안(6단계 보강, BUILD_SPEC 8.3). 예전의 "[제안]" 줄 파싱 대신 서버가 구조화해 준다
// (messages.suggestions: title, proposed_user_message, reason). 누르면 입력창에 문장을 채우고 사용자가 보낸다.
//
// 쓰는 곳: components/MessageList.tsx(suggestionMessageIds로 칩을 붙일 답변 고르기), SuggestionChips.tsx(Suggestion 타입).
// 칩을 누른 뒤 입력창 채우기는 components/ChatScreen.tsx(prefill)가 맡는다.
// 맞물린 API: GET /api/conversations/{id}/messages의 origin·suggestions(8단계 메시지 origin).
//
// 고칠 때 주의:
// - 제안을 자동으로 보내지 않는다. 자동 작업은 조회만 하고, 바꾸는 일은 사용자가 보낸 채팅으로 승인 카드를 거친다
//   (BUILD_SPEC 절대 원칙 7, DECISIONS 2026-11-17 6단계 화면 절).
// - origin 값("schedule"·"watch"·"chat")은 서버 메시지 origin과 글자로 맞물린다. 바뀌면 gen:api 뒤 여기를 본다.
// - 테스트: lib/suggestions.test.ts, components/SuggestionChips.test.tsx, components/MessageList.test.tsx.
import type { components } from "@/lib/api/schema";
import type { Message } from "@/lib/chat";

export type Suggestion = components["schemas"]["SuggestionOut"];

// 제안 버튼을 붙일 메시지 id. 예약 작업 결과(서버가 준 origin이 "schedule"인 답변)만 고른다.
// 조건 감시 알림(origin "watch")과 이 대화방에서 내가 보낸 채팅의 답변(origin "chat")은 고르지 않는다.
export function suggestionMessageIds(messages: readonly Message[]): Set<number> {
  const ids = messages
    .filter((message) => message.role === "assistant" && message.origin === "schedule")
    .map((message) => message.id);
  return new Set(ids);
}
