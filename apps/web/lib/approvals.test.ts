import { describe, expect, it } from "vitest";
import {
  approvalErrorMessage,
  formatRemaining,
  NETWORK_ERROR_CODE,
  readErrorCode,
} from "./approvals";

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

  it("오류 본문에서 detail.code를 꺼낸다(모양이 다르면 undefined)", () => {
    expect(readErrorCode({ detail: { code: "approval_expired", message: "…" } })).toBe(
      "approval_expired",
    );
    expect(readErrorCode({ detail: "문자열 오류" })).toBeUndefined();
    expect(readErrorCode(undefined)).toBeUndefined();
    expect(readErrorCode("text")).toBeUndefined();
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
