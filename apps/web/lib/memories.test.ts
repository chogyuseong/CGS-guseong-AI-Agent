import { describe, expect, it } from "vitest";
import { conversationHref, formatMemoryDate, formatMessageTime, memoryCountText } from "./memories";

describe("기억 화면 도우미", () => {
  it("저장한 날짜는 서울 기준 연·월·일", () => {
    expect(formatMemoryDate("2026-11-16T16:30:00Z")).toBe("2026년 11월 17일");
  });

  it("원래 메시지 시각은 서울 기준 날짜와 시각", () => {
    const text = formatMessageTime("2026-11-16T16:30:00Z");
    expect(text).toContain("2026년 11월 17일");
    expect(text).toContain("1:30");
  });

  it("대화 링크는 그 대화의 채팅 화면이다", () => {
    expect(conversationHref(12)).toBe("/chat/12");
  });

  it("저장 개수와 한도를 함께 보여 준다", () => {
    expect(memoryCountText(3, 200)).toBe("저장된 기억 3개 / 최대 200개");
  });
});
