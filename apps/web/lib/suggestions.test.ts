import { describe, expect, it } from "vitest";
import type { Message } from "@/lib/chat";
import { suggestionMessageIds } from "./suggestions";

describe("suggestionMessageIds: 칩을 붙일 메시지", () => {
  const base = { created_at: "2026-11-17T00:00:00Z", sources: [] };
  // 예약 작업 결과(서버 origin=schedule).
  const scheduled: Message = {
    ...base,
    id: 1,
    role: "assistant",
    content: "[제안] 예약 결과",
    task_id: 10,
    origin: "schedule",
  };
  // 조건 감시 알림(origin=watch).
  const watched: Message = {
    ...base,
    id: 2,
    role: "assistant",
    content: "[제안] 감시 알림 안",
    task_id: null,
    origin: "watch",
  };
  // 이 대화방에서 내가 보낸 채팅과 그 답변(origin=chat).
  const mine: Message = {
    ...base,
    id: 3,
    role: "user",
    content: "[제안] 내 메시지",
    task_id: 11,
    origin: "chat",
  };
  const chatAnswer: Message = { ...mine, id: 4, role: "assistant", content: "[제안] 채팅 답변 안" };

  it("예약 작업 결과(origin=schedule인 답변)에만 붙인다", () => {
    expect([...suggestionMessageIds([scheduled, watched, mine, chatAnswer])]).toEqual([1]);
  });

  it("모양이 아니라 origin으로 고른다: 내 메시지가 없는 채팅 답변에는 붙이지 않는다", () => {
    const orphanChat: Message = { ...chatAnswer, id: 5, task_id: 12 };
    expect([...suggestionMessageIds([orphanChat])]).toEqual([]);
  });

  it("origin이 schedule이어도 사용자 메시지에는 붙이지 않는다", () => {
    const odd: Message = { ...mine, id: 6, origin: "schedule" };
    expect([...suggestionMessageIds([odd])]).toEqual([]);
  });
});
