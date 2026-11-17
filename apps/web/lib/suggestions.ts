// 예약 작업 답변의 제안(6단계 보강, BUILD_SPEC 8.3). 예전의 "[제안]" 줄 파싱 대신 서버가 구조화해 준다
// (messages.suggestions: title, proposed_user_message, reason). 누르면 입력창에 문장을 채우고 사용자가 보낸다.
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
