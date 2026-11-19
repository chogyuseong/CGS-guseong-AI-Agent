import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { MemoriesSent } from "./MemoriesSent";

// 2026-11-19(분석 38번): 답변 아래에 이 작업을 시작할 때 모델에게 함께 보낸 기억을 접어서 보여 준다.
// '실제로 쓴' 기억이 아니라 '함께 보낸' 기억이라는 말을 그대로 쓴다(서버도 그것만 안다).
describe("MemoriesSent", () => {
  it("함께 보낸 기억 수와 내용, 기억 탭 링크를 보여 준다", () => {
    render(
      <MemoriesSent
        memories={[
          { id: 1, content: "매운 음식을 못 먹는다" },
          { id: 2, content: "채식을 한다" },
        ]}
      />,
    );
    expect(screen.getByText("이 답변을 만들 때 함께 보낸 기억 2개")).toBeInTheDocument();
    expect(screen.getByText("매운 음식을 못 먹는다")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "기억 탭에서 고치기" })).toHaveAttribute(
      "href",
      "/memory",
    );
  });

  it("보낸 기억이 없으면 아무것도 그리지 않는다", () => {
    const { container } = render(<MemoriesSent memories={[]} />);
    expect(container).toBeEmptyDOMElement();
  });
});
