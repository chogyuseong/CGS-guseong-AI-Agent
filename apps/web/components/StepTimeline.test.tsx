import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { Step } from "@/lib/chat";
import { StepTimeline } from "./StepTimeline";

// 가짜 단계 데이터를 만든다. 바꾸고 싶은 칸만 overrides로 넘긴다. (pytest의 팩토리 픽스처와 같은 역할)
function makeStep(overrides: Partial<Step>): Step {
  return {
    id: 1,
    order: 1,
    tool_name: "add_todo",
    label: "할 일 추가: 우유 사기",
    risk_level: "INTERNAL",
    status: "done",
    ok: true,
    input: { title: "우유 사기" },
    output: { ok: true, data: { id: 7 } },
    duration_ms: 12,
    ...overrides,
  };
}

describe("StepTimeline", () => {
  it("단계 상태마다 맞는 아이콘 이름을 보여 준다", () => {
    const steps = [
      makeStep({ id: 1, order: 1, status: "running", label: "진행 단계" }),
      makeStep({ id: 2, order: 2, status: "done", label: "완료 단계" }),
      makeStep({ id: 3, order: 3, status: "failed", label: "실패 단계" }),
      makeStep({ id: 4, order: 4, status: "waiting_approval", label: "승인 단계" }),
      makeStep({ id: 5, order: 5, status: "rejected", label: "거절 단계" }),
      makeStep({ id: 6, order: 6, status: "expired", label: "만료 단계" }),
    ];
    render(<StepTimeline steps={steps} />);

    for (const name of ["진행 중", "완료", "실패", "승인 대기", "거절됨", "만료됨"]) {
      expect(screen.getByRole("img", { name })).toBeInTheDocument();
    }
  });

  it("승인 대기·거절·만료 단계는 줄 끝에 상태 글자도 보여 준다", () => {
    render(
      <StepTimeline
        steps={[
          makeStep({ id: 1, order: 1, status: "waiting_approval", label: "일정 만들기" }),
          makeStep({ id: 2, order: 2, status: "expired", label: "일정 지우기" }),
        ]}
      />,
    );
    const waiting = screen.getByRole("button", { name: /일정 만들기/ });
    const expired = screen.getByRole("button", { name: /일정 지우기/ });
    expect(within(waiting).getByText("승인 대기")).toBeVisible();
    expect(within(expired).getByText("만료됨")).toBeVisible();
  });

  it("단계를 누르면 입력과 출력을 펼치고, 다시 누르면 접는다", () => {
    render(<StepTimeline steps={[makeStep({})]} />);
    const button = screen.getByRole("button", { name: /할 일 추가: 우유 사기/ });

    expect(button).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText(/"title": "우유 사기"/)).not.toBeInTheDocument();

    fireEvent.click(button);
    expect(button).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText(/"title": "우유 사기"/)).toBeInTheDocument();
    expect(screen.getByText(/"id": 7/)).toBeInTheDocument();

    fireEvent.click(button);
    expect(screen.queryByText(/"title": "우유 사기"/)).not.toBeInTheDocument();
  });

  it("단계가 없으면 아무것도 그리지 않는다", () => {
    const { container } = render(<StepTimeline steps={[]} />);
    expect(container).toBeEmptyDOMElement();
  });
});
