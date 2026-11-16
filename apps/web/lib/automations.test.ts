import { describe, expect, it } from "vitest";
import {
  activeCountText,
  automationErrorText,
  formatRunTime,
  intervalText,
  isAutomationRoom,
  shortText,
} from "./automations";

describe("automationErrorText: 켜기·끄기·삭제 실패 문구", () => {
  it("409 코드마다 다른 한국어 안내", () => {
    expect(
      automationErrorText({ kind: "schedule", status: 409, code: "limit_reached", maxActive: 10 }),
    ).toBe("켜 둘 수 있는 예약 작업은 최대 10개입니다. 다른 예약 작업을 먼저 꺼 주세요.");
    expect(
      automationErrorText({ kind: "watch", status: 409, code: "limit_reached", maxActive: 10 }),
    ).toBe("켜 둘 수 있는 조건 감시는 최대 10개입니다. 다른 감시를 먼저 꺼 주세요.");
    expect(
      automationErrorText({ kind: "watch", status: 409, code: "duplicate", maxActive: 10 }),
    ).toBe("같은 조회·조건으로 켜진 감시가 이미 있습니다. 그 감시를 쓰거나 먼저 꺼 주세요.");
    expect(
      automationErrorText({ kind: "watch", status: 409, code: "expired", maxActive: 10 }),
    ).toBe("만료된 감시는 다시 켤 수 없습니다. 채팅에서 새로 만들어 주세요.");
  });

  it("404는 이미 지워졌다고 알리고, 모르는 코드·상태는 상태 코드와 함께 일반 문구", () => {
    expect(
      automationErrorText({ kind: "schedule", status: 404, code: "not_found", maxActive: 10 }),
    ).toBe("이미 지워졌거나 찾을 수 없는 예약 작업입니다. 목록을 다시 불러왔습니다.");
    expect(
      automationErrorText({ kind: "watch", status: 409, code: "이상한값", maxActive: 10 }),
    ).toBe("요청을 처리하지 못했습니다 (오류 409). 잠시 후 다시 시도해 주세요.");
    expect(automationErrorText({ kind: "watch", status: 500, code: null, maxActive: 10 })).toBe(
      "요청을 처리하지 못했습니다 (오류 500). 잠시 후 다시 시도해 주세요.",
    );
  });
});

describe("표시용 글자", () => {
  it("시각은 서울 기준 '11월 18일(수) 07:30'", () => {
    expect(formatRunTime("2026-11-17T22:30:00Z")).toBe("11월 18일(수) 07:30");
    expect(formatRunTime("시각 아님")).toBe("시각 아님");
  });

  it("확인 간격", () => {
    expect(intervalText(30)).toBe("30분마다");
    expect(intervalText(60)).toBe("1시간마다");
    expect(intervalText(90)).toBe("1시간 30분마다");
    expect(intervalText(1440)).toBe("24시간마다");
  });

  it("켜진 개수 / 최대", () => {
    expect(activeCountText("예약 작업", 3, 10)).toBe("켜진 예약 작업 3개 / 최대 10개");
  });

  it("긴 답변은 앞부분만(줄바꿈은 공백으로)", () => {
    expect(shortText("첫 줄\n둘째 줄", 100)).toBe("첫 줄 둘째 줄");
    expect(shortText("가".repeat(10), 5)).toBe("가가가가가…");
  });

  it("'예약 작업' 대화방은 제목으로 알아본다", () => {
    expect(isAutomationRoom("예약 작업")).toBe(true);
    expect(isAutomationRoom("새 대화")).toBe(false);
    expect(isAutomationRoom(undefined)).toBe(false);
  });
});
