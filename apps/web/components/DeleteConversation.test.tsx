import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { DeleteConversation } from "./DeleteConversation";

const neverResolves = () => new Promise<never>(() => {});

describe("대화 지우기(5단계 보강)", () => {
  it("바로 지우지 않고, 지워지는 것과 남는 것을 알린 뒤 한 번 더 묻는다", () => {
    const onDelete = vi.fn(neverResolves);
    render(<DeleteConversation onDelete={onDelete} />);

    fireEvent.click(screen.getByRole("button", { name: "이 대화 지우기" }));

    const box = screen.getByRole("group", { name: "대화 지우기 확인" });
    expect(box).toHaveTextContent("메시지와 작업 기록");
    expect(box).toHaveTextContent("이 대화에서 만든 기억은 남습니다");
    expect(box).toHaveTextContent("외부 결과는 그대로 남습니다");
    expect(box).toHaveTextContent("백업에는 바로 지워지지 않고");
    expect(onDelete).not.toHaveBeenCalled();
    fireEvent.click(within(box).getByRole("button", { name: "취소" }));
    expect(screen.getByRole("button", { name: "이 대화 지우기" })).toBeInTheDocument();
  });

  it("지우기를 누르면 한 번만 보내고 처리 중에는 막는다", () => {
    const onDelete = vi.fn(neverResolves);
    render(<DeleteConversation onDelete={onDelete} />);
    fireEvent.click(screen.getByRole("button", { name: "이 대화 지우기" }));
    const submit = screen.getByRole("button", { name: "지우기" });

    fireEvent.click(submit);
    fireEvent.click(submit);

    expect(onDelete).toHaveBeenCalledTimes(1);
    expect(submit).toBeDisabled();
  });

  it("진행 중인 작업이 있어 거부되면 서버 문장을 보여 준다", async () => {
    const onDelete = vi
      .fn()
      .mockRejectedValue(new Error("진행 중인 작업이 있어 대화를 지울 수 없습니다."));
    render(<DeleteConversation onDelete={onDelete} />);
    fireEvent.click(screen.getByRole("button", { name: "이 대화 지우기" }));

    fireEvent.click(screen.getByRole("button", { name: "지우기" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("진행 중인 작업이 있어");
  });
});
