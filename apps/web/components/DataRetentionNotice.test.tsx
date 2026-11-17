import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { DataRetentionNotice } from "./DataRetentionNotice";

describe("데이터 보관 안내(5단계 보강)", () => {
  it("보관 기간과 삭제 범위, 백업 지연을 숨기지 않고 알린다", () => {
    render(<DataRetentionNotice />);

    const region = screen.getByRole("region", { name: "데이터 보관" });
    expect(region).toHaveTextContent("작업 실행 기록은 끝난 뒤 7일");
    expect(region).toHaveTextContent("실행 영수증은 90일");
    expect(region).toHaveTextContent("대화를 지워도 그 대화에서 만든 기억은 남습니다");
    expect(region).toHaveTextContent("기억을 지워도 예전 대화에 같은 내용이 남아 있으면");
    expect(region).toHaveTextContent("백업에는 최대 30일 동안 남을 수 있습니다");
  });
});
