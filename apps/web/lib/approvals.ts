// 승인 카드가 쓰는 서버 호출과 문구 함수. 타입은 pnpm gen:api가 만든 schema.d.ts에서 꺼낸다.
//
// 쓰는 곳: components/ApprovalCard.tsx(문구·만료 시각·실행 결과 한 줄), components/TaskApprovals.tsx
// (useDecideApproval), PendingTask·TaskSteps·작업 기록 탭이 같은 카드를 그린다(DECISIONS 2026-11-17 화면).
// 맞물린 API: POST /api/approvals/{approval_id}/approve(본문 confirm_mismatch)·/reject·/revise(본문 note),
// 결과는 GET /api/tasks/{id}의 approvals와 같은 ApprovalOut 모양이다.
// 근거: BUILD_SPEC 절대 원칙 3·4·10(승인·고정 인자·영수증 근거 성공 표시), DECISIONS 2026-11-17
// "승인 API 오류는 detail: {code, message}", "mismatch 승인은 confirm_mismatch 없으면 409".
//
// 고칠 때 주의:
// - ERROR_MESSAGES의 code 글자는 서버 apps/api/app/agent/approvals.py의 오류 code,
//   app/schemas/tasks.py의 ApprovalErrorCode와 글자로 맞물린다. 서버에 code를 더하면 gen:api 뒤 여기에 문구를 더한다.
// - operation_status 값(EXECUTION_TEXTS)은 서버 operation 상태(docs/STATE_MACHINES.md)와 맞물린다.
// - 캐시 키는 lib/chat.ts의 chatKeys를 쓴다. 키 모양을 바꾸면 onMutate·onSettled도 같이 본다.
// - 테스트: lib/approvals.test.ts, components/ApprovalCard.test.tsx, components/PendingTask.test.tsx, e2e/approval.spec.ts.
// 변경 이력:
// - 2026-11-18 '고쳐서 다시'(요구사항 밖, 사용자 결정 DECISIONS 2026-11-18): Decision.revisionNote가 있으면 /revise로
//   보낸다(메모를 붙인 거절). 서버가 인자를 고치지 않고 모델이 다시 계획해 새 카드를 만든다.
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import { chatKeys, type Task } from "@/lib/chat";

type Schemas = components["schemas"];
export type Approval = Schemas["ApprovalOut"];
export type ApprovalStatus = Schemas["ApprovalStatus"];
export type GuardVerdict = Schemas["VerdictKind"];
export type ApprovalDecisionOut = Schemas["ApprovalDecisionOut"];
type ApprovalErrorOut = Schemas["ApprovalErrorOut"];
type ApprovalErrorCode = Schemas["ApprovalErrorCode"];

// 카드가 서버에 보내는 결정. confirmMismatch는 mismatch 승인을 사용자가 한 번 더 확인했다는 표시다.
// revisionNote는 '고쳐서 다시'의 고칠 내용(approve=false일 때만 쓴다. 서버는 앞뒤 공백을 지우고 1~300자를 받는다).
export type Decision = { approve: boolean; confirmMismatch: boolean; revisionNote?: string };
// 서버 ReviseRequest.note의 최대 길이(apps/api/app/schemas/tasks.py, approvals.revision_note 칸 길이와 같다).
export const REVISION_NOTE_MAX = 300;

// network는 서버 code가 아니라 화면이 붙이는 값이다(fetch 자체가 실패했을 때). 서버 code와 겹치지 않게 둔다.
// MISMATCH_CODE는 ApprovalCard가 "한 번 더 확인" 영역을 여는 신호라 서버 글자와 반드시 같아야 한다.
export const NETWORK_ERROR_CODE = "network";
export const MISMATCH_CODE = "mismatch_confirmation_required";

// 승인·거절 실패. code는 서버의 detail.code(또는 network)이고, message는 화면에 보여 줄 한국어 문장이다.
export class ApprovalActionError extends Error {
  constructor(
    readonly code: string,
    message: string,
  ) {
    super(message);
    this.name = "ApprovalActionError";
  }
}

// 문구가 아니라 code로 분기한다(docs/DECISIONS.md 2단계: 승인 API 오류는 detail: {code, message}).
const NOT_FOUND_MESSAGE = "승인 요청을 찾을 수 없습니다.";
const NETWORK_MESSAGE = "서버에 연결하지 못했습니다. 네트워크를 확인해 주세요.";
const ERROR_MESSAGES = new Map<string, string>([
  [MISMATCH_CODE, "요청과 다를 수 있습니다. 그래도 진행할지 한 번 더 확인해 주세요."],
  ["task_not_waiting", "이 작업은 더 이상 승인을 기다리지 않습니다."],
  ["approval_expired", "승인 시간이 지나 작업이 취소되었습니다."],
  ["not_found", NOT_FOUND_MESSAGE],
  [NETWORK_ERROR_CODE, NETWORK_MESSAGE],
]);

/**
 * 실패 응답을 화면 문구로 바꾼다. 아는 code가 먼저이고, code가 없는 404(옛 서버·프록시)는 "찾을 수 없음",
 * 그 밖은 상태 번호를 넣은 일반 문구다. 서버의 message는 쓰지 않는다(화면 문구를 한 곳에서 관리하려고).
 */
export function approvalErrorMessage(status: number, code: string | undefined): string {
  const known = code === undefined ? undefined : ERROR_MESSAGES.get(code);
  if (known !== undefined) return known;
  if (status === 404) return NOT_FOUND_MESSAGE;
  return `승인을 처리하지 못했습니다 (오류 ${status}). 잠시 후 다시 시도해 주세요.`;
}

// 오류 본문에서 detail.code를 꺼낸다. 3단계부터 OpenAPI 문서에 ApprovalErrorOut(404·409·410)이 있어
// 생성된 타입을 그대로 쓴다. 422(HTTPValidationError)는 detail이 배열이라 code가 없다.
// 본문이 JSON이 아닌 응답(프록시의 502 등)도 올 수 있어 객체인지는 실행 중에 한 번 더 본다.
export function approvalErrorCode(
  body: ApprovalErrorOut | Schemas["HTTPValidationError"] | undefined,
): ApprovalErrorCode | undefined {
  if (typeof body !== "object" || body === null) return undefined;
  const detail = body.detail;
  if (detail === undefined || Array.isArray(detail)) return undefined;
  return detail.code;
}

// 결정 응답(승인 한 건 + 그 시점 task 상태)을 task 캐시에 바로 반영한다. 다시 가져오기 전에도
// 카드와 진행 문구("이어서 처리하는 중")가 어긋나지 않게 하려는 것이다. 원래 객체는 바꾸지 않는다.
export function applyDecision(task: Task, result: ApprovalDecisionOut): Task {
  return {
    ...task,
    status: result.task_status,
    approvals: task.approvals.map((item) =>
      item.id === result.approval.id ? result.approval : item,
    ),
  };
}

// 만료까지 남은 시간을 분 단위 한국어로. now는 Date.now() 값(테스트에서 고정하려고 받는다).
export function formatRemaining(expiresAt: string, now: number): string {
  const remainingMs = new Date(expiresAt).getTime() - now;
  if (remainingMs <= 0) return "만료 시간이 지났습니다";
  if (remainingMs < 60_000) return "1분 미만 남음";
  return `${Math.floor(remainingMs / 60_000)}분 남음`;
}

// 2단계 보강: 승인 뒤 외부 실행 결과 한 줄. "실행 완료"는 영수증(receipt_confirmed)이 있을 때만이다(절대 원칙 10).
// 결정 전·거절·작업 취소, 계획이 없는 옛 승인은 null(카드 머리의 상태로 충분하다).
type ExecutionFields = Pick<
  Approval,
  "status" | "operation_status" | "receipt_confirmed" | "execute_before"
>;
const UNCONFIRMED = "결과 확인 필요 · 실행됐는지 확인하지 못했습니다";
const EXECUTION_TEXTS: Partial<Record<NonNullable<Approval["operation_status"]>, string>> = {
  executing: "실행 중",
  unknown: UNCONFIRMED,
  reconciling: UNCONFIRMED,
  failed: "실행하지 못했습니다",
  expired: "승인 후 실행 가능 시간이 지나 실행하지 않았습니다",
  stale: "도구가 바뀌어 실행하지 않았습니다. 다시 요청해 주세요",
};

export function executionText(approval: ExecutionFields): string | null {
  const operation = approval.operation_status;
  if (approval.status !== "approved" || operation === null) return null;
  if (operation === "succeeded") {
    return approval.receipt_confirmed ? "실행 완료 · 외부 서비스에서 확인함" : UNCONFIRMED;
  }
  if (operation === "approved" && approval.execute_before) {
    return `${formatExpiryTime(approval.execute_before)}까지 실행합니다`;
  }
  return EXECUTION_TEXTS[operation] ?? null;
}

// 서버 시각(UTC ISO)을 서울 시각 "오후 3:05" 모양으로. 기기 시간대와 무관하게 서울로 고정한다(앱 전체가 서울 기준).
export function formatExpiryTime(expiresAt: string): string {
  return new Date(expiresAt).toLocaleTimeString("ko-KR", {
    timeZone: "Asia/Seoul",
    hour: "numeric",
    minute: "2-digit",
  });
}

/**
 * 승인·거절 요청을 보내고 ApprovalDecisionOut을 돌려준다. 실패는 모두 ApprovalActionError로 던진다
 * (code: 서버 detail.code, 네트워크면 "network", 모르면 "unknown").
 * confirm_mismatch는 사용자가 카드에서 한 번 더 확인했을 때만 true다. 화면이 이 값을 자동으로 true로 보내면
 * 서버의 mismatch 재확인(절대 원칙 15: 승인 자동 통과 금지)을 우회하게 되므로 그런 코드를 두지 않는다.
 */
async function postDecision(approvalId: number, decision: Decision) {
  const result = await sendDecision(approvalId, decision).catch(() => {
    // 네트워크가 끊기면 fetch가 영어 오류를 던지므로 한국어 문구로 바꾼다.
    throw new ApprovalActionError(NETWORK_ERROR_CODE, NETWORK_MESSAGE);
  });
  if (result.data) return result.data;
  const code = approvalErrorCode(result.error);
  throw new ApprovalActionError(
    code ?? "unknown",
    approvalErrorMessage(result.response.status, code),
  );
}

// 결정 종류에 맞는 경로로 보낸다: 승인 /approve, 메모가 있는 거절 /revise, 그냥 거절 /reject.
function sendDecision(approvalId: number, decision: Decision) {
  const path = { approval_id: approvalId };
  if (decision.approve) {
    return api.POST("/api/approvals/{approval_id}/approve", {
      params: { path },
      body: { confirm_mismatch: decision.confirmMismatch },
    });
  }
  if (decision.revisionNote !== undefined) {
    return api.POST("/api/approvals/{approval_id}/revise", {
      params: { path },
      body: { note: decision.revisionNote },
    });
  }
  return api.POST("/api/approvals/{approval_id}/reject", { params: { path } });
}

type TaskRef = { id: number; conversation_id: number };

// 승인 한 건의 결정. 3단계 서버는 마지막 결정이면 재개 워크플로를 시작만 하고 바로 응답한다.
// 응답을 받으면 task 캐시에 먼저 반영해(카드 → "승인함", 진행 문구 → "이어서 처리하는 중") 화면이 끊기지 않게 하고,
// 성공·실패 모두 task, 메시지, 작업 기록을 다시 가져온다(오류 뒤에도 최신 상태를 보여 주려고).
// 재개가 끝날 때까지의 진행은 useTask의 폴링이 이어서 보여 준다.
export function useDecideApproval(task: TaskRef, approvalId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (decision: Decision) => postDecision(approvalId, decision),
    // 결정 전에 시작된 폴링 응답이 늦게 도착해 방금 반영한 결과를 덮어쓰지 않도록 진행 중인 요청을 취소한다.
    onMutate: () => queryClient.cancelQueries({ queryKey: chatKeys.task(task.id) }),
    onSuccess: (result) => {
      queryClient.setQueryData<Task>(chatKeys.task(task.id), (current) =>
        current === undefined ? current : applyDecision(current, result),
      );
    },
    onSettled: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: chatKeys.task(task.id) }),
        queryClient.invalidateQueries({ queryKey: chatKeys.messages(task.conversation_id) }),
        queryClient.invalidateQueries({ queryKey: chatKeys.tasks }),
      ]);
    },
  });
}
