import { describe, expect, it } from "vitest";
import { parseSuggestions, suggestionMessageIds } from "./suggestions";

describe("parseSuggestions: [제안] 줄 찾기", () => {
  it("줄 처음의 [제안] 뒤 문장을 꺼낸다(앞뒤 공백은 지운다)", () => {
    const content = "오늘 일정은 2개입니다.\n[제안] 금요일 저녁 7시에 저녁 약속 일정 잡아줘  ";
    expect(parseSuggestions(content)).toEqual(["금요일 저녁 7시에 저녁 약속 일정 잡아줘"]);
  });

  it("여러 줄이면 나온 순서대로 모두 꺼낸다(\\r\\n 줄바꿈도)", () => {
    const content =
      "요약\r\n[제안] 우유 사기 할 일에 추가해줘\r\n중간 줄\n[제안]내일 우산 챙기라고 기억해";
    expect(parseSuggestions(content)).toEqual([
      "우유 사기 할 일에 추가해줘",
      "내일 우산 챙기라고 기억해",
    ]);
  });

  it("줄 중간에 있는 [제안]은 버튼으로 만들지 않는다", () => {
    const content = "비가 옵니다. [제안] 우산 챙기기\n- [제안] 목록 기호 뒤\n제안: 아님";
    expect(parseSuggestions(content)).toEqual([]);
  });

  it("줄 앞의 공백(들여쓰기)은 줄 처음으로 본다", () => {
    expect(parseSuggestions("  [제안] 들여쓴 제안")).toEqual(["들여쓴 제안"]);
  });

  it("문장이 비어 있거나 공백뿐인 [제안] 줄은 뺀다", () => {
    expect(parseSuggestions("[제안]\n[제안]    \n[제안] 진짜 제안")).toEqual(["진짜 제안"]);
  });

  it("같은 문장이 두 번 나오면 한 번만 꺼낸다", () => {
    expect(parseSuggestions("[제안] 같은 문장\n[제안]  같은 문장")).toEqual(["같은 문장"]);
  });

  it("보낼 수 없는 길이(4,000자 초과)의 문장은 뺀다", () => {
    const long = "가".repeat(4001);
    expect(parseSuggestions(`[제안] ${long}\n[제안] 짧은 문장`)).toEqual(["짧은 문장"]);
  });

  it("[제안] 줄이 없으면 빈 목록", () => {
    expect(parseSuggestions("오늘은 일정이 없습니다.")).toEqual([]);
    expect(parseSuggestions("")).toEqual([]);
  });
});

describe("suggestionMessageIds: 칩을 붙일 메시지", () => {
  const base = { created_at: "2026-11-17T00:00:00Z" };
  const messages = [
    // 예약 작업 결과: 같은 task_id의 내 메시지가 없다.
    { ...base, id: 1, role: "assistant" as const, content: "[제안] 예약 결과 제안", task_id: 10 },
    // 조건 감시 알림: task가 없다.
    { ...base, id: 2, role: "assistant" as const, content: "[제안] 감시 알림 안", task_id: null },
    // 이 대화방에서 내가 보낸 채팅과 그 답변.
    { ...base, id: 3, role: "user" as const, content: "[제안] 내 메시지", task_id: 11 },
    { ...base, id: 4, role: "assistant" as const, content: "[제안] 채팅 답변 안", task_id: 11 },
  ];

  it("내 메시지 없이 생긴 task의 답변(예약 작업 결과)에만 붙인다", () => {
    expect([...suggestionMessageIds(messages)]).toEqual([1]);
  });
});
