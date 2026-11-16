import { describe, expect, it } from "vitest";
import { fieldLabel, formatDateTimeText, formatFieldValue, toolDisplayName } from "./toolText";

describe("formatDateTimeText", () => {
  it("도구 입력 형식(서울 기준 YYYY-MM-DDTHH:MM)을 연·월·일(요일) 시:분으로 바꾼다", () => {
    expect(formatDateTimeText("2026-11-20T19:00")).toBe("2026년 11월 20일(금) 19:00");
    expect(formatDateTimeText("2026-11-22T07:05:00")).toBe("2026년 11월 22일(일) 07:05");
  });

  it("시간대가 붙은 ISO 시각은 서울 시각으로 바꿔 보여 준다", () => {
    expect(formatDateTimeText("2026-11-20T19:00:00+09:00")).toBe("2026년 11월 20일(금) 19:00");
    expect(formatDateTimeText("2026-11-20T10:00:00Z")).toBe("2026년 11월 20일(금) 19:00");
  });

  it("시각 모양이 아니거나 없는 날짜면 null(원래 글자를 그대로 쓴다)", () => {
    expect(formatDateTimeText("2026-02-30T10:00")).toBeNull();
    expect(formatDateTimeText("2026-11-20")).toBeNull();
    expect(formatDateTimeText("저녁 7시")).toBeNull();
  });
});

describe("필드 이름과 값", () => {
  it("아는 칸 이름은 한국어로, 모르는 칸은 원래 이름으로", () => {
    expect(fieldLabel("title")).toBe("제목");
    expect(fieldLabel("start")).toBe("시작");
    expect(fieldLabel("event_id")).toBe("일정 ID");
    expect(fieldLabel("something_new")).toBe("something_new");
  });

  it("값은 사람이 읽는 글자로 바꾼다", () => {
    expect(formatFieldValue("2026-11-20T19:00")).toBe("2026년 11월 20일(금) 19:00");
    expect(formatFieldValue("성수동")).toBe("성수동");
    expect(formatFieldValue(null)).toBe("없음");
    expect(formatFieldValue(true)).toBe("예");
    expect(formatFieldValue(false)).toBe("아니요");
    expect(formatFieldValue(3)).toBe("3");
    expect(formatFieldValue({ a: 1 })).toBe('{"a":1}');
  });

  it("메일 도구 결과의 칸 이름도 한국어로(7단계)", () => {
    expect(fieldLabel("from")).toBe("보낸 사람");
    expect(fieldLabel("subject")).toBe("제목");
    expect(fieldLabel("received_at")).toBe("받은 시각");
    expect(fieldLabel("messages")).toBe("메일");
    expect(fieldLabel("attachments")).toBe("첨부(이름만)");
    expect(toolDisplayName("read_mail")).toBe("메일 읽기 (read_mail)");
  });

  it("캘린더 도구는 한국어 이름과 원래 이름을 함께 보여 준다", () => {
    expect(toolDisplayName("create_event")).toBe("캘린더 일정 만들기 (create_event)");
    expect(toolDisplayName("delete_event")).toBe("캘린더 일정 지우기 (delete_event)");
    expect(toolDisplayName("fake_tool")).toBe("fake_tool");
  });
});
