import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ChatInput } from "./ChatInput";

describe("ChatInput", () => {
  it("전송 중에는 입력창과 전송 버튼을 막고 진행 중 표시를 보여 준다", () => {
    render(<ChatInput onSend={vi.fn()} pending />);

    expect(screen.getByLabelText("메시지 입력")).toBeDisabled();
    expect(screen.getByRole("button", { name: "전송" })).toBeDisabled();
    expect(screen.getByRole("status")).toHaveTextContent("답변을 기다리는 중");
  });

  it("내용이 공백뿐이면 전송 버튼을 막는다", () => {
    render(<ChatInput onSend={vi.fn()} pending={false} />);
    fireEvent.change(screen.getByLabelText("메시지 입력"), { target: { value: "   " } });

    expect(screen.getByRole("button", { name: "전송" })).toBeDisabled();
  });

  it("전송하면 앞뒤 공백을 뺀 내용을 넘기고, 성공하면 입력창을 비운다", async () => {
    const onSend = vi.fn().mockResolvedValue(undefined);
    render(<ChatInput onSend={onSend} pending={false} />);
    const input = screen.getByLabelText("메시지 입력");

    fireEvent.change(input, { target: { value: "  내 할 일 보여줘 " } });
    fireEvent.click(screen.getByRole("button", { name: "전송" }));

    expect(onSend).toHaveBeenCalledWith("내 할 일 보여줘");
    await waitFor(() => expect(input).toHaveValue(""));
  });

  it("전송이 실패하면 입력한 내용을 그대로 남긴다", async () => {
    const onSend = vi.fn().mockRejectedValue(new Error("실패"));
    render(<ChatInput onSend={onSend} pending={false} />);
    const input = screen.getByLabelText("메시지 입력");

    fireEvent.change(input, { target: { value: "다시 보낼 내용" } });
    fireEvent.click(screen.getByRole("button", { name: "전송" }));

    await waitFor(() => expect(onSend).toHaveBeenCalled());
    expect(input).toHaveValue("다시 보낼 내용");
  });
});
