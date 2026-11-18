import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { Suggestion } from "@/lib/suggestions";
import { SuggestionChips } from "./SuggestionChips";

const UMBRELLA: Suggestion = {
  title: "우산 챙기기",
  proposed_user_message: "내일 아침 '우산 챙기기' 할 일 추가해줘",
  reason: "내일 강수 확률이 70%입니다.",
};

describe("SuggestionChips(6단계 보강: 구조화된 제안)", () => {
  it("이름·이유·보낼 문장을 글자로 보여 주고, 저절로 보내지 않는다", () => {
    const onPick = vi.fn();
    render(<SuggestionChips suggestions={[UMBRELLA]} onPick={onPick} />);

    expect(screen.getByText("우산 챙기기")).toBeInTheDocument();
    expect(screen.getByText("내일 강수 확률이 70%입니다.")).toBeInTheDocument();
    expect(screen.getByText("내일 아침 '우산 챙기기' 할 일 추가해줘")).toBeInTheDocument();
    expect(screen.getByText(/누르면 입력창에 채웁니다/)).toBeInTheDocument();
    expect(onPick).not.toHaveBeenCalled();
  });

  it("누르면 문장을 입력창에 채우도록 넘긴다(보내지 않는다)", () => {
    const onPick = vi.fn();
    render(<SuggestionChips suggestions={[UMBRELLA]} onPick={onPick} />);

    fireEvent.click(screen.getByRole("button", { name: "우산 챙기기: 입력창에 채우기" }));

    expect(onPick).toHaveBeenCalledWith("내일 아침 '우산 챙기기' 할 일 추가해줘");
  });

  it("같은 제안이 두 번 와도 key가 겹치지 않고 둘 다 그린다", () => {
    const errors = vi.spyOn(console, "error").mockImplementation(() => {});
    render(<SuggestionChips suggestions={[UMBRELLA, UMBRELLA]} onPick={vi.fn()} />);

    expect(screen.getAllByRole("button", { name: "우산 챙기기: 입력창에 채우기" })).toHaveLength(2);
    expect(errors.mock.calls.flat().join(" ")).not.toMatch(/same key/);
    errors.mockRestore();
  });

  it("제안이 없으면 아무것도 그리지 않는다", () => {
    const { container } = render(<SuggestionChips suggestions={[]} onPick={vi.fn()} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("HTML처럼 보이는 글자는 글자 그대로 보여 준다", () => {
    render(
      <SuggestionChips suggestions={[{ ...UMBRELLA, title: "<b>굵게</b>" }]} onPick={vi.fn()} />,
    );
    expect(screen.getByText("<b>굵게</b>")).toBeInTheDocument();
  });
});
