// 승인 카드가 쓰는 서버 호출과 문구 함수. 타입은 pnpm gen:api가 만든 schema.d.ts에서 꺼낸다.
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import { chatKeys } from "@/lib/chat";

type Schemas = components["schemas"];
export type Approval = Schemas["ApprovalOut"];
export type ApprovalStatus = Schemas["ApprovalStatus"];
export type GuardVerdict = Schemas["VerdictKind"];

// 카드가 서버에 보내는 결정. confirmMismatch는 mismatch 승인을 사용자가 한 번 더 확인했다는 표시다.
export type Decision = { approve: boolean; confirmMismatch: boolean };

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

export function approvalErrorMessage(status: number, code: string | undefined): string {
  const known = code === undefined ? undefined : ERROR_MESSAGES.get(code);
  if (known !== undefined) return known;
  if (status === 404) return NOT_FOUND_MESSAGE;
  return `승인을 처리하지 못했습니다 (오류 ${status}). 잠시 후 다시 시도해 주세요.`;
}

// 오류 본문 {"detail": {"code": ...}}에서 code를 꺼낸다. OpenAPI 문서에 오류 본문 모양이 없어서
// (생성된 타입이 content?: never) unknown으로 받아 실행 중에 모양을 확인한다.
export function readErrorCode(body: unknown): string | undefined {
  if (typeof body !== "object" || body === null || !("detail" in body)) return undefined;
  const detail = body.detail;
  if (typeof detail !== "object" || detail === null || !("code" in detail)) return undefined;
  return typeof detail.code === "string" ? detail.code : undefined;
}

// 만료까지 남은 시간을 분 단위 한국어로. now는 Date.now() 값(테스트에서 고정하려고 받는다).
export function formatRemaining(expiresAt: string, now: number): string {
  const remainingMs = new Date(expiresAt).getTime() - now;
  if (remainingMs <= 0) return "만료 시간이 지났습니다";
  if (remainingMs < 60_000) return "1분 미만 남음";
  return `${Math.floor(remainingMs / 60_000)}분 남음`;
}

export function formatExpiryTime(expiresAt: string): string {
  return new Date(expiresAt).toLocaleTimeString("ko-KR", {
    timeZone: "Asia/Seoul",
    hour: "numeric",
    minute: "2-digit",
  });
}

async function postDecision(approvalId: number, decision: Decision) {
  const path = { approval_id: approvalId };
  const request = decision.approve
    ? api.POST("/api/approvals/{approval_id}/approve", {
        params: { path },
        body: { confirm_mismatch: decision.confirmMismatch },
      })
    : api.POST("/api/approvals/{approval_id}/reject", { params: { path } });
  const result = await request.catch(() => {
    // 네트워크가 끊기면 fetch가 영어 오류를 던지므로 한국어 문구로 바꾼다.
    throw new ApprovalActionError(NETWORK_ERROR_CODE, NETWORK_MESSAGE);
  });
  if (result.data) return result.data;
  const code = readErrorCode(result.error as unknown);
  throw new ApprovalActionError(
    code ?? "unknown",
    approvalErrorMessage(result.response.status, code),
  );
}

type TaskRef = { id: number; conversation_id: number };

// 승인 한 건의 결정. 마지막 결정이면 서버가 재개 run을 끝낸 뒤 응답하므로 몇 초 걸릴 수 있다.
// 성공·실패 모두 task, 메시지, 작업 기록을 다시 가져온다(오류 뒤에도 최신 상태를 보여 주려고).
export function useDecideApproval(task: TaskRef, approvalId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (decision: Decision) => postDecision(approvalId, decision),
    onSettled: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: chatKeys.task(task.id) }),
        queryClient.invalidateQueries({ queryKey: chatKeys.messages(task.conversation_id) }),
        queryClient.invalidateQueries({ queryKey: chatKeys.tasks }),
      ]);
    },
  });
}
