import { describe, expect, it } from "vitest";
import {
  APPROVAL_POLL_MS,
  backoffInterval,
  createPhaseClock,
  POLLING_QUERY_OPTIONS,
  taskListPollInterval,
  taskPollInterval,
} from "./polling";

// 9단계 보강: 폴링 간격 규칙(BUILD_SPEC 10.1 "폴링"). 간격 표는 docs/DECISIONS.md에 있다.
describe("실행 중 백오프", () => {
  it("1초에서 시작해 시간이 지날수록 2초, 3초, 5초로 늘린다", () => {
    expect(backoffInterval(0)).toBe(1000);
    expect(backoffInterval(9_999)).toBe(1000);
    expect(backoffInterval(10_000)).toBe(2000);
    expect(backoffInterval(29_999)).toBe(2000);
    expect(backoffInterval(30_000)).toBe(3000);
    expect(backoffInterval(60_000)).toBe(5000);
    expect(backoffInterval(30 * 60_000)).toBe(5000);
  });

  it("음수(시계가 거꾸로 감)는 0으로 본다", () => {
    expect(backoffInterval(-5000)).toBe(1000);
  });
});

describe("task 한 건의 폴링 간격", () => {
  it("대기·진행·취소 요청·재개 중이면 백오프를 따른다", () => {
    for (const phase of ["queued", "running", "cancelling", "resuming"] as const) {
      expect(taskPollInterval(phase, 0)).toBe(1000);
      expect(taskPollInterval(phase, 120_000)).toBe(5000);
    }
  });

  it("사용자 결정을 기다리는 승인 대기는 처음부터 느리게(15초) 갱신하고, 30분이 지나도 1초가 아니다", () => {
    expect(APPROVAL_POLL_MS).toBe(15_000);
    expect(taskPollInterval("waiting_approval", 0)).toBe(15_000);
    expect(taskPollInterval("waiting_approval", 30 * 60_000)).toBe(15_000);
  });

  it("끝난 상태와 아직 받지 못한 상태는 폴링하지 않는다", () => {
    expect(taskPollInterval("finished", 0)).toBe(false);
    expect(taskPollInterval("starting", 0)).toBe(false);
  });
});

describe("작업 기록 목록의 폴링 간격", () => {
  it("진행 중인 task가 하나라도 있으면 3초", () => {
    expect(taskListPollInterval(["done", "running"])).toBe(3000);
    expect(taskListPollInterval(["waiting_approval", "queued"])).toBe(3000);
    expect(taskListPollInterval(["cancel_requested"])).toBe(3000);
  });

  it("남은 것이 승인 대기뿐이면 15초", () => {
    expect(taskListPollInterval(["done", "waiting_approval"])).toBe(15_000);
  });

  it("모두 끝났거나 목록이 없으면 멈춘다", () => {
    expect(taskListPollInterval(["done", "failed", "cancelled"])).toBe(false);
    expect(taskListPollInterval([])).toBe(false);
    expect(taskListPollInterval(undefined)).toBe(false);
  });
});

describe("단계 시계(백오프 기준 시각)", () => {
  it("같은 단계가 이어지면 처음 본 시각부터 잰다", () => {
    const clock = createPhaseClock();
    expect(clock.elapsed("running", 1_000)).toBe(0);
    expect(clock.elapsed("running", 13_000)).toBe(12_000);
  });

  it("단계가 바뀌면(승인 대기 → 재개) 다시 0부터 잰다", () => {
    const clock = createPhaseClock();
    clock.elapsed("running", 0);
    clock.elapsed("waiting_approval", 90_000);
    expect(clock.elapsed("resuming", 200_000)).toBe(0);
    expect(clock.elapsed("resuming", 201_000)).toBe(1_000);
  });
});

describe("탭이 보이지 않을 때와 다시 연결될 때", () => {
  it("숨은 탭에서는 폴링하지 않고, 다시 보이거나 다시 연결되면 바로 서버 상태를 가져온다", () => {
    expect(POLLING_QUERY_OPTIONS).toEqual({
      refetchIntervalInBackground: false,
      refetchOnWindowFocus: "always",
      refetchOnReconnect: "always",
    });
  });
});
