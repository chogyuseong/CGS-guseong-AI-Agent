import { describe, expect, it } from "vitest";
import type { Approval } from "./approvals";
import {
  isTaskFinished,
  openScheduleTaskIds,
  PHASE_TEXT,
  sendErrorMessage,
  type TaskStatus,
  taskPhase,
  taskRefetchInterval,
} from "./chat";

function approvalWith(status: Approval["status"]): Approval {
  return {
    id: 1,
    step_id: 1,
    tool_name: "create_event",
    tool_input: {},
    status,
    summary: "일정을 만듭니다.",
    expires_at: "2026-11-17T03:30:00Z",
    decided_at: null,
    guard: null,
    needs_mismatch_confirmation: false,
  };
}

const taskOf = (status: TaskStatus, approvals: Approval[] = []) => ({ status, approvals });

describe("진행 단계 판단(3단계: 즉시 응답 + 폴링)", () => {
  it("아직 task를 못 가져왔으면 starting, 대기·진행 중이면 queued·running이다", () => {
    expect(taskPhase(undefined)).toBe("starting");
    expect(taskPhase(taskOf("queued"))).toBe("queued");
    expect(taskPhase(taskOf("running"))).toBe("running");
  });

  it("승인 대기 중 결정할 승인이 남아 있으면 waiting_approval이다", () => {
    const task = taskOf("waiting_approval", [approvalWith("approved"), approvalWith("pending")]);
    expect(taskPhase(task)).toBe("waiting_approval");
  });

  it("승인을 모두 결정했는데 서버가 아직 재개 전이면 resuming(이어서 처리하는 중)이다", () => {
    expect(taskPhase(taskOf("waiting_approval", [approvalWith("approved")]))).toBe("resuming");
    expect(taskPhase(taskOf("waiting_approval", [approvalWith("rejected")]))).toBe("resuming");
  });

  it("끝난 상태면 finished이고, 끝나지 않은 단계마다 보여 줄 문구가 있다", () => {
    for (const status of ["done", "failed", "cancelled", "budget_exceeded"] as const) {
      expect(taskPhase(taskOf(status))).toBe("finished");
    }
    expect(PHASE_TEXT.queued).toContain("곧 시작");
    expect(PHASE_TEXT.running).toContain("작업하는 중");
    expect(PHASE_TEXT.resuming).toContain("이어서 처리하는 중");
  });

  it("승인을 모두 결정해 재개를 기다리는 동안은 3초가 아니라 1초마다 다시 가져온다", () => {
    expect(taskRefetchInterval("waiting_approval", [approvalWith("approved")])).toBe(1000);
    expect(taskRefetchInterval("waiting_approval", [approvalWith("pending")])).toBe(3000);
  });
});

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

describe("openScheduleTaskIds: 답변이 아직 없는 예약 작업 task", () => {
  const base = {
    request_text: null,
    final_answer: null,
    error: null,
    step_count: 0,
    created_at: "2026-11-17T00:00:00Z",
    updated_at: "2026-11-17T00:00:00Z",
  };
  const tasks = [
    { ...base, id: 1, conversation_id: 5, origin: "schedule" as const, status: "running" as const },
    { ...base, id: 2, conversation_id: 5, origin: "schedule" as const, status: "done" as const },
    { ...base, id: 3, conversation_id: 6, origin: "schedule" as const, status: "queued" as const },
    { ...base, id: 4, conversation_id: 5, origin: "chat" as const, status: "running" as const },
    { ...base, id: 5, conversation_id: 5, origin: "schedule" as const, status: "queued" as const },
    { ...base, id: 7, conversation_id: 5, origin: "schedule" as const, status: "queued" as const },
  ];
  const answered = [
    { id: 1, role: "assistant" as const, content: "끝", task_id: 5, created_at: base.created_at },
  ];

  it("이 대화방의 끝나지 않은 예약 task 중 답변이 없는 것만, 오래된 것(id 작은 것)부터", () => {
    expect(openScheduleTaskIds([...tasks].reverse(), 5, answered)).toEqual([1, 7]);
    expect(openScheduleTaskIds(undefined, 5, answered)).toEqual([]);
  });
});
