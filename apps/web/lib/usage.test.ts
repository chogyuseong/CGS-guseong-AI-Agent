import { describe, expect, it } from "vitest";
import {
  barPercent,
  formatTokens,
  formatUsageDay,
  formatUsd,
  isUsageEmpty,
  purposeInfo,
  todayPercentText,
  todayRatio,
  type Usage,
} from "./usage";

describe("formatUsd: 달러 표기", () => {
  it("소수 4자리까지 보여 준다", () => {
    expect(formatUsd(0.0123)).toBe("$0.0123");
    expect(formatUsd(1)).toBe("$1.0000");
    expect(formatUsd(0.12345)).toBe("$0.1235");
  });

  it("0보다 크지만 $0.0001보다 작은 금액은 0으로 보이지 않게 '미만'으로 쓴다", () => {
    expect(formatUsd(0.000004)).toBe("$0.0001 미만");
    expect(formatUsd(0.00009)).toBe("$0.0001 미만");
  });

  it("0은 $0.0000, 천 단위에는 쉼표", () => {
    expect(formatUsd(0)).toBe("$0.0000");
    expect(formatUsd(1234.5)).toBe("$1,234.5000");
  });

  it("음수나 숫자가 아닌 값은 $0.0000으로 본다(서버가 주지 않지만 화면이 깨지지 않게)", () => {
    expect(formatUsd(-0.5)).toBe("$0.0000");
    expect(formatUsd(Number.NaN)).toBe("$0.0000");
  });
});

describe("formatTokens: 토큰 표기", () => {
  it("천 단위 쉼표", () => {
    expect(formatTokens(0)).toBe("0");
    expect(formatTokens(1234567)).toBe("1,234,567");
  });
});

describe("formatUsageDay: 날짜 표기(서울 날짜 그대로)", () => {
  it("월·일·요일", () => {
    expect(formatUsageDay("2026-11-17")).toBe("11월 17일(화)");
    expect(formatUsageDay("2026-01-01")).toBe("1월 1일(목)");
  });
});

describe("오늘 한도 사용률", () => {
  const today = { date: "2026-11-17", limit_usd: 1, remaining_usd: 0.75, exceeded: false };

  it("쓴 금액 / 한도 비율, 1을 넘지 않는다", () => {
    expect(todayRatio({ ...today, cost_usd: 0.25 })).toBe(0.25);
    expect(todayRatio({ ...today, cost_usd: 3 })).toBe(1);
  });

  it("한도가 0이면 쓴 금액이 있을 때 가득 찬 것으로 본다", () => {
    expect(todayRatio({ ...today, limit_usd: 0, cost_usd: 0 })).toBe(0);
    expect(todayRatio({ ...today, limit_usd: 0, cost_usd: 0.01 })).toBe(1);
  });

  it("퍼센트 글자: 조금이라도 썼으면 0%가 아니라 '1% 미만'", () => {
    expect(todayPercentText({ ...today, cost_usd: 0 })).toBe("0%");
    expect(todayPercentText({ ...today, cost_usd: 0.001 })).toBe("1% 미만");
    expect(todayPercentText({ ...today, cost_usd: 0.256 })).toBe("25%");
    expect(todayPercentText({ ...today, cost_usd: 2 })).toBe("100%");
  });
});

describe("barPercent: 일별 막대 길이", () => {
  it("가장 큰 날을 100으로 본다", () => {
    expect(barPercent(0.5, 1)).toBe(50);
    expect(barPercent(1, 1)).toBe(100);
  });

  it("쓴 날은 아주 작아도 보이게 최소 2, 안 쓴 날과 전부 0인 기간은 0", () => {
    expect(barPercent(0.000001, 1)).toBe(2);
    expect(barPercent(0, 1)).toBe(0);
    expect(barPercent(0, 0)).toBe(0);
  });
});

describe("purposeInfo: 용도 이름", () => {
  it("네 용도는 한국어 이름과 설명이 있다", () => {
    expect(purposeInfo("agent").name).toBe("에이전트");
    expect(purposeInfo("guard").name).toBe("감시 에이전트(승인 검사)");
    expect(purposeInfo("watch").name).toBe("조건 감시 판정");
    expect(purposeInfo("eval").name).toBe("평가");
    for (const purpose of ["agent", "guard", "watch", "eval"]) {
      expect(purposeInfo(purpose).description).not.toBe("");
    }
  });

  it("모르는 용도는 서버 값을 이름으로 쓴다", () => {
    expect(purposeInfo("new_purpose")).toEqual({ name: "new_purpose", description: "" });
  });
});

describe("isUsageEmpty: 빈 상태", () => {
  const zero = { input_tokens: 0, output_tokens: 0, total_tokens: 0, cost_usd: 0, calls: 0 };
  const usage = (calls: number): Pick<Usage, "total"> => ({ total: { ...zero, calls } });

  it("기간 안에 호출이 하나도 없으면 빈 상태", () => {
    expect(isUsageEmpty(usage(0))).toBe(true);
    expect(isUsageEmpty(usage(1))).toBe(false);
  });
});
