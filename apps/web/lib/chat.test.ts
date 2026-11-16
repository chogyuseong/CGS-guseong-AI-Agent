import { describe, expect, it } from "vitest";
import { isTaskFinished, sendErrorMessage, taskRefetchInterval } from "./chat";

describe("task 다시 가져오기 판단", () => {
  it("끝난 상태(done, failed, cancelled, budget_exceeded)면 끝났다고 본다", () => {
    for (const status of ["done", "failed", "cancelled", "budget_exceeded"] as const) {
      expect(isTaskFinished(status)).toBe(true);
    }
  });

  it("queued, running, waiting_approval은 아직 끝나지 않았다고 본다", () => {
    for (const status of ["queued", "running", "waiting_approval"] as const) {
      expect(isTaskFinished(status)).toBe(false);
    }
  });

  it("진행 중이면 1초마다, 끝났거나 아직 모르면 다시 가져오지 않는다", () => {
    expect(taskRefetchInterval("queued")).toBe(1000);
    expect(taskRefetchInterval("running")).toBe(1000);
    expect(taskRefetchInterval("done")).toBe(false);
    expect(taskRefetchInterval("cancelled")).toBe(false);
    expect(taskRefetchInterval(undefined)).toBe(false);
  });

  it("승인 대기 중이면 3초마다 다시 가져온다(다른 기기에서 결정해도 반영되도록 멈추지는 않는다)", () => {
    expect(taskRefetchInterval("waiting_approval")).toBe(3000);
  });
});

describe("전송 오류 문구", () => {
  it("상태 코드마다 한국어 문구를 돌려준다", () => {
    expect(sendErrorMessage(422)).toContain("4,000자");
    expect(sendErrorMessage(429)).toContain("1분");
    expect(sendErrorMessage(500)).toContain("500");
  });
});
