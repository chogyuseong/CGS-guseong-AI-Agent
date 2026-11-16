// 예약 작업 답변의 [제안] 줄(6단계). 명세: 자동 작업은 조회만 하고, 행동은 "[제안] 요청 문장" 한 줄로 남긴다.
// 화면은 그 줄을 버튼으로 그리고, 사용자가 누를 때만 그 문장을 일반 채팅 메시지로 보낸다(그 뒤는 평소처럼 승인).
// [제안] 줄은 서버가 따로 필드로 주지 않는다. 답변(messages.content)에서 화면이 직접 찾는다.
// 어떤 답변이 예약 작업 결과인지는 서버의 messages.origin으로 안다(9단계부터, 그 전에는 모양으로 추측).
import type { Message } from "@/lib/chat";

export const SUGGESTION_PREFIX = "[제안]";
// 채팅 메시지 한 건의 최대 길이(서버 MessageCreate). 이보다 길면 보낼 수 없어 버튼을 만들지 않는다.
const MAX_SENDABLE = 4000;

// 줄 처음(앞 공백은 허용)이 "[제안]"인 줄만 고른다. 문장 중간이나 목록 기호("- [제안]") 뒤는 고르지 않는다.
// 문장이 비었거나 너무 긴 줄은 빼고, 같은 문장은 한 번만 남긴다.
export function parseSuggestions(content: string): string[] {
  const found: string[] = [];
  for (const line of content.split(/\r?\n/)) {
    const trimmed = line.trimStart();
    if (!trimmed.startsWith(SUGGESTION_PREFIX)) continue;
    const sentence = trimmed.slice(SUGGESTION_PREFIX.length).trim();
    if (sentence === "" || sentence.length > MAX_SENDABLE) continue;
    if (!found.includes(sentence)) found.push(sentence);
  }
  return found;
}

// [제안] 버튼을 붙일 메시지 id. 예약 작업 결과(서버가 준 origin이 "schedule"인 답변)만 고른다.
// 조건 감시 알림(origin "watch")은 외부 조회 결과를 판정 모델이 요약한 글이라 버튼을 만들지 않는다.
// 이 대화방에서 내가 보낸 채팅의 답변(origin "chat")도 고르지 않는다(명세는 예약 작업 답변의 [제안]만 버튼으로 그린다).
export function suggestionMessageIds(messages: readonly Message[]): Set<number> {
  const ids = messages
    .filter((message) => message.role === "assistant" && message.origin === "schedule")
    .map((message) => message.id);
  return new Set(ids);
}
