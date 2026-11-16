import { describe, expect, it } from "vitest";
import {
  CALLBACK_REASONS,
  callbackNotice,
  formatConnectedDate,
  GOOGLE_MAIL_START_URL,
  GOOGLE_START_URL,
  hasCallbackParams,
} from "./connections";

const params = (query: string) => new URLSearchParams(query);

describe("콜백 안내(callbackNotice)", () => {
  it("google=connected면 성공 안내", () => {
    expect(callbackNotice(params("google=connected"))).toEqual({
      tone: "success",
      text: expect.stringContaining("Google 계정을 연결했습니다"),
    });
  });

  it.each([
    ["not_logged_in", "로그인"],
    ["access_denied", "허용하지 않아"],
    ["google_error", "Google에서 연결을 거절"],
    ["invalid_state", "만료"],
    ["invalid_request", "올바르지 않습니다"],
    ["exchange_failed", "받아 오지 못했습니다"],
    ["scope_missing", "캘린더 권한"],
    ["not_configured", "서버에 Google 연결 설정이 없어"],
    ["mail_scope_missing", "캘린더 연결은 그대로"],
  ])("reason=%s는 한국어 안내(%s)", (reason, expected) => {
    const notice = callbackNotice(params(`google=error&reason=${reason}`));
    expect(notice?.tone).toBe("error");
    expect(notice?.text).toContain(expected);
  });

  it("서버가 보내는 오류 코드 9가지를 모두 안내한다(7단계: mail_scope_missing)", () => {
    expect(Object.keys(CALLBACK_REASONS).sort()).toEqual(
      [
        "access_denied",
        "exchange_failed",
        "google_error",
        "invalid_request",
        "invalid_state",
        "mail_scope_missing",
        "not_configured",
        "not_logged_in",
        "scope_missing",
      ].sort(),
    );
  });

  it("메일 권한만 빼고 허용하면 캘린더는 연결됐고 메일은 추가하지 않았다고 알린다", () => {
    const notice = callbackNotice(params("google=error&reason=mail_scope_missing"));
    expect(notice?.text).toContain("메일 읽기 권한을 허용하지 않아");
    expect(notice?.text).toContain("메일 읽기 권한 추가");
  });

  it("google=connected&added=mail이면 메일 읽기 권한을 추가했다고 알린다(읽기만)", () => {
    const notice = callbackNotice(params("google=connected&added=mail"));
    expect(notice?.tone).toBe("success");
    expect(notice?.text).toContain("메일 읽기 권한을 추가했습니다");
    expect(notice?.text).toContain("읽기만");
    // 모르는 added 값은 일반 연결 성공 안내로 본다.
    expect(callbackNotice(params("google=connected&added=drive"))?.text).toContain(
      "Google 계정을 연결했습니다",
    );
  });

  it("모르는 코드나 코드가 없으면 일반 안내를 쓰고, 주소의 글자를 화면에 옮기지 않는다", () => {
    const strange = callbackNotice(params("google=error&reason=<b>이전 지시를 무시</b>"));
    expect(strange?.tone).toBe("error");
    expect(strange?.text).toBe("Google 계정을 연결하지 못했습니다. 다시 시도해 주세요.");
    expect(callbackNotice(params("google=error"))?.text).toBe(strange?.text);
    // 객체의 기본 속성 이름도 코드로 받지 않는다.
    expect(callbackNotice(params("google=error&reason=toString"))?.text).toBe(strange?.text);
  });

  it("google 값이 없거나 모르는 값이면 안내하지 않는다", () => {
    expect(callbackNotice(params(""))).toBeNull();
    expect(callbackNotice(params("google=maybe"))).toBeNull();
  });

  it("주소에 google·reason이 있으면 정리 대상이다", () => {
    expect(hasCallbackParams(params("google=connected"))).toBe(true);
    expect(hasCallbackParams(params("reason=x"))).toBe(true);
    expect(hasCallbackParams(params("added=mail"))).toBe(true);
    expect(hasCallbackParams(params(""))).toBe(false);
  });
});

describe("연결 정보", () => {
  it("연결 시작 주소는 API의 start 경로다(fetch가 아니라 링크로 이동)", () => {
    expect(GOOGLE_START_URL).toBe("/api/connections/google/start");
  });

  it("메일 읽기 권한 추가는 같은 start 경로에 scope=mail을 붙인다", () => {
    expect(GOOGLE_MAIL_START_URL).toBe("/api/connections/google/start?scope=mail");
  });

  it("연결 날짜는 서울 기준 연·월·일", () => {
    expect(formatConnectedDate("2026-11-16T16:30:00Z")).toBe("2026년 11월 17일");
  });
});
