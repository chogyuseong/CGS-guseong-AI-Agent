import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { Step } from "@/lib/chat";
import { StepTimeline } from "./StepTimeline";

// 생활 비서 D(2026-11-18): show_job_prep 결과는 단계를 펼친 카드로, list_experiences는 읽은 개수만 보인다.
function makeStep(overrides: Partial<Step>): Step {
  return {
    id: 1,
    order: 1,
    tool_name: "show_job_prep",
    label: "SK 지원 준비 카드 만드는 중",
    risk_level: "READ",
    status: "done",
    ok: true,
    input: { company: "SK" },
    output: null,
    duration_ms: 5,
    ...overrides,
  };
}

const CARD = {
  회사: "SK",
  직무: "백엔드 개발자",
  대조: [
    {
      요구: "Python 기반 웹 서비스 개발 경험",
      구분: "필수",
      근거: [{ 경험: "마이에이전트", 인용: "FastAPI로 승인 흐름을 설계했다", 설명: "설계 경험" }],
    },
    { 요구: "Kubernetes 운영 경험", 구분: "우대", 근거: [] },
  ],
  부족: ["Kubernetes 운영 경험 없음"],
  "예상 질문": ["중복 실행을 어떻게 막았나요?", "중복 실행을 어떻게 막았나요?"],
  자소서: ["실행 영수증 설계를 문제 해결 사례로"],
  체크리스트: ["Kubernetes 기초 실습"],
};

describe("JobPrepCard", () => {
  it("준비 카드를 처음부터 펼쳐 다섯 칸을 보여 주고 입력 JSON은 숨긴다", () => {
    render(<StepTimeline steps={[makeStep({ output: { status: "succeeded", data: CARD } })]} />);

    expect(screen.getByText("SK · 백엔드 개발자 지원 준비")).toBeInTheDocument();
    const table = screen.getByRole("region", { name: "요구 역량 대조" });
    expect(within(table).getByText("Python 기반 웹 서비스 개발 경험")).toBeInTheDocument();
    expect(within(table).getByText(/FastAPI로 승인 흐름을 설계했다/)).toBeInTheDocument();
    expect(within(table).getByText("맞는 경험 없음")).toBeInTheDocument();
    for (const title of ["부족한 점", "예상 면접 질문", "자소서 방향", "준비 체크리스트"]) {
      expect(screen.getByRole("region", { name: title })).toBeInTheDocument();
    }
    // 같은 질문이 두 번 와도 둘 다 보인다(key 겹침 없음).
    expect(screen.getAllByText("중복 실행을 어떻게 막았나요?")).toHaveLength(2);
    expect(screen.queryByText("입력")).not.toBeInTheDocument();
  });

  it("내 경험 읽기 단계는 내용 없이 개수만 보인다", () => {
    const listed = makeStep({
      tool_name: "list_experiences",
      label: "저장한 내 경험 읽는 중",
      output: {
        status: "succeeded",
        data: { notice: "x", content: { stored: false, experience_count: 2 }, truncated: false },
      },
    });
    render(<StepTimeline steps={[listed]} />);

    fireEvent.click(screen.getByRole("button", { name: /저장한 내 경험 읽는 중/ }));

    expect(
      screen.getByText("저장한 경험 2개를 읽었습니다. 작업 기록에는 내용을 남기지 않습니다."),
    ).toBeInTheDocument();
  });

  it("실패한 준비 카드 단계는 카드 대신 출력 JSON을 보여 준다", () => {
    const failed = makeStep({
      status: "failed",
      ok: false,
      output: { status: "failed", user_message: "요구 사항 1을 찾지 못했습니다." },
    });
    render(<StepTimeline steps={[failed]} />);

    expect(screen.queryByText(/지원 준비$/)).not.toBeInTheDocument();
    expect(screen.getByText(/요구 사항 1을 찾지 못했습니다/)).toBeInTheDocument();
  });
});
