import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { SuggestionChips } from "./SuggestionChips";

const SENTENCE = "금요일 저녁 7시에 성수동에서 저녁 약속 일정 잡아줘";

describe("SuggestionChips", () => {
  it("제안 문장 전체를 버튼 글자로 보여 주고, 저절로 보내지 않는다", () => {
    const onSend = vi.fn().mockResolvedValue(undefined);
    render(
      <SuggestionChips suggestions={[SENTENCE, "우유 사기 할 일에 추가해줘"]} onSend={onSend} />,
    );

    expect(screen.getByRole("button", { name: SENTENCE })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "우유 사기 할 일에 추가해줘" })).toBeInTheDocument();
    expect(screen.getByText(/누르면 이 문장을 채팅으로 보냅니다/)).toBeInTheDocument();
    expect(onSend).not.toHaveBeenCalled();
  });

  it("누르면 그 문장을 한 번만 보낸다(연타해도 한 번). 보낸 뒤에는 '보냄'으로 막는다", async () => {
    const onSend = vi.fn().mockResolvedValue(undefined);
    render(<SuggestionChips suggestions={[SENTENCE]} onSend={onSend} />);
    const chip = screen.getByRole("button", { name: SENTENCE });

    fireEvent.click(chip);
    fireEvent.click(chip);
    fireEvent.click(chip);

    await waitFor(() => expect(screen.getByRole("button", { name: SENTENCE })).toBeDisabled());
    expect(onSend).toHaveBeenCalledTimes(1);
    expect(onSend).toHaveBeenCalledWith(SENTENCE);
    expect(screen.getByRole("button", { name: SENTENCE })).toHaveTextContent("보냄");
  });

  it("보내는 동안 다른 제안도 막는다", () => {
    const onSend = vi.fn(() => new Promise<never>(() => {}));
    render(<SuggestionChips suggestions={[SENTENCE, "둘째 제안"]} onSend={onSend} />);

    fireEvent.click(screen.getByRole("button", { name: SENTENCE }));
    fireEvent.click(screen.getByRole("button", { name: "둘째 제안" }));

    expect(onSend).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "둘째 제안" })).toBeDisabled();
  });

  it("밖에서 전송 중이면(disabled) 누를 수 없다", () => {
    const onSend = vi.fn();
    render(<SuggestionChips suggestions={[SENTENCE]} onSend={onSend} disabled />);
    fireEvent.click(screen.getByRole("button", { name: SENTENCE }));
    expect(onSend).not.toHaveBeenCalled();
  });

  it("보내기에 실패하면 다시 누를 수 있다", async () => {
    const onSend = vi.fn().mockRejectedValueOnce(new Error("실패")).mockResolvedValue(undefined);
    render(<SuggestionChips suggestions={[SENTENCE]} onSend={onSend} />);
    const chip = screen.getByRole("button", { name: SENTENCE });

    fireEvent.click(chip);
    await waitFor(() => expect(chip).toBeEnabled());
    fireEvent.click(chip);

    await waitFor(() => expect(onSend).toHaveBeenCalledTimes(2));
  });

  it("태그 모양의 문장도 글자 그대로 보인다", () => {
    const tricky = "<img src=x onerror=alert(1)> 일정 지워줘";
    const { container } = render(<SuggestionChips suggestions={[tricky]} onSend={vi.fn()} />);
    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByRole("button", { name: tricky })).toBeInTheDocument();
  });

  it("제안이 없으면 아무것도 그리지 않는다", () => {
    const { container } = render(<SuggestionChips suggestions={[]} onSend={vi.fn()} />);
    expect(container).toBeEmptyDOMElement();
  });
});
