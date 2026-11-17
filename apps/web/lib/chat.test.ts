import { describe, expect, it, vi } from "vitest";
import type { Approval } from "./approvals";
import {
  countUserMessages,
  pendingBubbleText,
  SEND_ATTEMPTS,
  sendWithRetry,
  isTaskFinished,
  openScheduleTaskIds,
  PHASE_TEXT,
  sendErrorMessage,
  type TaskStatus,
  taskPhase,
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
    target: "외부 서비스",
    account: null,
    execute_before: null,
    operation_status: null,
    receipt_confirmed: false,
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
  // 폴링 간격 규칙은 lib/polling.test.ts(9단계 보강)에 있다.
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
    {
      id: 1,
      role: "assistant" as const,
      content: "끝",
      task_id: 5,
      created_at: base.created_at,
      origin: "schedule" as const,
      sources: [],
    },
  ];

  it("이 대화방의 끝나지 않은 예약 task 중 답변이 없는 것만, 오래된 것(id 작은 것)부터", () => {
    expect(openScheduleTaskIds([...tasks].reverse(), 5, answered)).toEqual([1, 7]);
    expect(openScheduleTaskIds(undefined, 5, answered)).toEqual([]);
  });
});

describe("sendWithRetry: 네트워크 오류면 같은 client_request_id로 다시 보낸다", () => {
  const ok = { data: { task_id: 1 }, response: new Response(null, { status: 202 }) };

  it("두 번 끊긴 뒤 세 번째에 성공하면 세 번 모두 같은 id를 쓴다", async () => {
    const post = vi
      .fn()
      .mockRejectedValueOnce(new TypeError("fetch failed"))
      .mockRejectedValueOnce(new TypeError("fetch failed"))
      .mockResolvedValueOnce(ok);

    const result = await sendWithRetry(post, "req-1");

    expect(result).toBe(ok);
    expect(post.mock.calls).toEqual([["req-1"], ["req-1"], ["req-1"]]);
  });

  it("계속 끊기면 정해진 횟수만 보내고 한국어 오류를 던진다", async () => {
    const post = vi.fn().mockRejectedValue(new TypeError("fetch failed"));

    await expect(sendWithRetry(post, "req-2")).rejects.toThrow("서버에 연결하지 못했습니다");
    expect(post).toHaveBeenCalledTimes(SEND_ATTEMPTS);
  });

  it("서버가 오류로 답하면(4xx) 다시 보내지 않는다", async () => {
    const refused = { data: undefined, response: new Response(null, { status: 429 }) };
    const post = vi.fn().mockResolvedValue(refused);

    expect(await sendWithRetry(post, "req-3")).toBe(refused);
    expect(post).toHaveBeenCalledTimes(1);
  });
});

describe("pendingBubbleText: '보내는 중' 말풍선", () => {
  const user = (id: number, content: string) => ({
    id,
    role: "user" as const,
    content,
    task_id: id,
    created_at: "2026-11-17T03:00:00Z",
    origin: "chat" as const,
    sources: [],
  });

  it("보내는 중이고 아직 목록에 새 내 메시지가 없으면 보낸 문장을 보여 준다", () => {
    const messages = [user(1, "어제 말")];
    expect(pendingBubbleText(true, "새 말", countUserMessages(messages), messages)).toBe("새 말");
  });

  it("목록을 다시 받아 내 메시지가 늘었으면 숨긴다(같은 문장이 두 번 보이지 않게)", () => {
    const before = [user(1, "어제 말")];
    const after = [...before, user(2, "새 말")];
    expect(pendingBubbleText(true, "새 말", countUserMessages(before), after)).toBeUndefined();
  });

  it("보내는 중이 아니면 보여 주지 않는다", () => {
    expect(pendingBubbleText(false, "새 말", 0, [])).toBeUndefined();
  });
});
