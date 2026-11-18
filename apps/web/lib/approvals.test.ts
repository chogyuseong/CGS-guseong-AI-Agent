import { describe, expect, it } from "vitest";
import {
  type Approval,
  applyDecision,
  approvalErrorCode,
  approvalErrorMessage,
  executionText,
  formatRemaining,
  NETWORK_ERROR_CODE,
} from "./approvals";
import type { Task } from "./chat";

describe("승인 오류 문구", () => {
  it("서버의 detail.code마다 한국어 문구를 돌려준다", () => {
    expect(approvalErrorMessage(409, "task_not_waiting")).toBe(
      "이 작업은 더 이상 승인을 기다리지 않습니다.",
    );
    expect(approvalErrorMessage(410, "approval_expired")).toBe(
      "승인 시간이 지나 작업이 취소되었습니다.",
    );
    expect(approvalErrorMessage(409, "mismatch_confirmation_required")).toContain("한 번 더 확인");
    expect(approvalErrorMessage(404, "not_found")).toBe("승인 요청을 찾을 수 없습니다.");
  });

  it("네트워크 오류와 알 수 없는 오류도 한국어로 알려 준다", () => {
    expect(approvalErrorMessage(0, NETWORK_ERROR_CODE)).toContain("서버에 연결하지 못했습니다");
    expect(approvalErrorMessage(500, undefined)).toContain("500");
  });

  it("오류 본문(생성된 ApprovalErrorOut 타입)에서 detail.code를 꺼낸다(검증 오류·본문 없음은 undefined)", () => {
    expect(approvalErrorCode({ detail: { code: "approval_expired", message: "…" } })).toBe(
      "approval_expired",
    );
    expect(
      approvalErrorCode({ detail: [{ loc: ["body"], msg: "잘못된 값", type: "value_error" }] }),
    ).toBeUndefined();
    expect(approvalErrorCode(undefined)).toBeUndefined();
  });
});

describe("결정 결과를 task 캐시에 바로 반영", () => {
  const pending: Approval = {
    id: 5,
    step_id: 1,
    tool_name: "create_event",
    tool_input: {},
    status: "pending",
    summary: "일정을 만듭니다.",
    expires_at: "2026-11-17T03:30:00Z",
    decided_at: null,
    guard: null,
    needs_mismatch_confirmation: false,
    target: "Google 캘린더(기본 캘린더)",
    account: null,
    execute_before: null,
    operation_status: "awaiting_approval",
    receipt_confirmed: false,
    revision_note: null,
  };
  const other: Approval = { ...pending, id: 6 };
  const task: Task = {
    id: 1,
    conversation_id: 2,
    origin: "chat",
    status: "waiting_approval",
    final_answer: null,
    error: null,
    request_count: 1,
    total_tokens: 10,
    created_at: "2026-11-17T03:00:00Z",
    updated_at: "2026-11-17T03:00:00Z",
    steps: [],
    approvals: [pending, other],
  };

  it("응답의 승인으로 같은 id 카드만 바꾸고 task 상태를 응답의 task_status로 바꾼다", () => {
    const decided: Approval = { ...pending, status: "approved" };
    const next = applyDecision(task, { approval: decided, changed: true, task_status: "running" });

    expect(next.status).toBe("running");
    expect(next.approvals.map((item) => item.status)).toEqual(["approved", "pending"]);
    expect(task.approvals[0]?.status).toBe("pending"); // 원래 객체는 바꾸지 않는다
  });
});

describe("만료까지 남은 시간", () => {
  const expiresAt = "2026-11-17T03:30:00Z";
  const at = (iso: string) => new Date(iso).getTime();

  it("분 단위로 남은 시간을 알려 준다", () => {
    expect(formatRemaining(expiresAt, at("2026-11-17T03:07:00Z"))).toBe("23분 남음");
    expect(formatRemaining(expiresAt, at("2026-11-17T03:28:30Z"))).toBe("1분 남음");
  });

  it("1분이 안 남았거나 지났으면 그렇게 알려 준다", () => {
    expect(formatRemaining(expiresAt, at("2026-11-17T03:29:30Z"))).toBe("1분 미만 남음");
    expect(formatRemaining(expiresAt, at("2026-11-17T03:31:00Z"))).toBe("만료 시간이 지났습니다");
  });
});

describe("실행 결과 문구(영수증 기준)", () => {
  const base = {
    status: "approved",
    operation_status: "approved",
    receipt_confirmed: false,
    execute_before: null,
  } as const;

  it("영수증이 있을 때만 실행 완료라고 한다", () => {
    expect(executionText({ ...base, operation_status: "succeeded", receipt_confirmed: true })).toBe(
      "실행 완료 · 외부 서비스에서 확인함",
    );
    expect(
      executionText({ ...base, operation_status: "succeeded", receipt_confirmed: false }),
    ).toMatch(/^결과 확인 필요/);
  });

  it("상태마다 한국어로 알려 준다", () => {
    expect(executionText({ ...base, operation_status: "executing" })).toBe("실행 중");
    expect(executionText({ ...base, operation_status: "unknown" })).toMatch(/^결과 확인 필요/);
    expect(executionText({ ...base, operation_status: "reconciling" })).toMatch(/^결과 확인 필요/);
    expect(executionText({ ...base, operation_status: "failed" })).toBe("실행하지 못했습니다");
    expect(executionText({ ...base, operation_status: "expired" })).toBe(
      "승인 후 실행 가능 시간이 지나 실행하지 않았습니다",
    );
    expect(executionText({ ...base, operation_status: "stale" })).toBe(
      "도구가 바뀌어 실행하지 않았습니다. 다시 요청해 주세요",
    );
  });

  it("승인했지만 아직 실행 전이면 실행 마감 시각을 알려 주고, 결정 전·거절·옛 승인은 null", () => {
    const approved = { ...base, execute_before: "2026-11-17T03:06:00Z" };
    expect(executionText(approved)).toMatch(/까지 실행합니다$/);
    expect(
      executionText({ ...base, status: "pending", operation_status: "awaiting_approval" }),
    ).toBeNull();
    expect(executionText({ ...base, status: "rejected", operation_status: "rejected" })).toBeNull();
    expect(executionText({ ...base, operation_status: null })).toBeNull();
  });
});
