import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { type Approval, ApprovalActionError } from "@/lib/approvals";
import { ApprovalCard } from "./ApprovalCard";

// 가짜 승인 데이터. 바꾸고 싶은 칸만 overrides로 넘긴다.
function makeApproval(overrides: Partial<Approval> = {}): Approval {
  return {
    id: 31,
    step_id: 7,
    tool_name: "create_event",
    tool_input: { title: "저녁 약속", start: "2026-11-20T19:00" },
    status: "pending",
    summary: "11월 20일(금) 19:00~21:00 '저녁 약속' 일정을 기본 캘린더에 만듭니다.",
    expires_at: new Date(Date.now() + 25 * 60_000).toISOString(),
    decided_at: null,
    guard: { verdict: "match", reason: "요청한 일정과 같습니다." },
    needs_mismatch_confirmation: false,
    ...overrides,
  };
}

const neverResolves = () => new Promise<never>(() => {});

describe("ApprovalCard", () => {
  it("대기 중이면 할 일, 감시 판정, 만료까지 남은 시간, 승인·거절 버튼을 보여 준다", () => {
    render(<ApprovalCard approval={makeApproval()} onDecide={vi.fn()} />);
    const card = screen.getByRole("article", { name: "승인 요청" });

    expect(card).toHaveTextContent("'저녁 약속' 일정을 기본 캘린더에 만듭니다.");
    expect(card).toHaveTextContent("요청과 일치");
    expect(card).toHaveTextContent(/2[45]분 남음/);
    expect(within(card).getByRole("button", { name: "승인" })).toBeEnabled();
    expect(within(card).getByRole("button", { name: "거절" })).toBeEnabled();
    // 도구 입력값은 접어 둔다(펼치면 보인다).
    expect(within(card).getByText("입력값 보기")).toBeInTheDocument();
    expect(card).toHaveTextContent("저녁 약속");
  });

  it("감시 판정마다 다른 배지를 보여 주고, mismatch면 이유를 함께 보여 준다", () => {
    const { rerender } = render(
      <ApprovalCard
        approval={makeApproval({ guard: { verdict: "unsure", reason: "정보가 부족합니다." } })}
        onDecide={vi.fn()}
      />,
    );
    expect(screen.getByText("확인 필요")).toBeInTheDocument();

    rerender(
      <ApprovalCard
        approval={makeApproval({
          guard: { verdict: "mismatch", reason: "요청에 없는 날짜입니다." },
          needs_mismatch_confirmation: true,
        })}
        onDecide={vi.fn()}
      />,
    );
    expect(screen.getByText("요청과 다를 수 있음")).toBeInTheDocument();
    expect(screen.getByText("요청에 없는 날짜입니다.")).toBeInTheDocument();
  });

  it("mismatch면 승인 전에 한 번 더 묻고, 확인하면 confirmMismatch=true로 보낸다", () => {
    const onDecide = vi.fn(neverResolves);
    render(
      <ApprovalCard
        approval={makeApproval({
          guard: { verdict: "mismatch", reason: "요청에 없는 날짜입니다." },
          needs_mismatch_confirmation: true,
        })}
        onDecide={onDecide}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "승인" }));
    expect(onDecide).not.toHaveBeenCalled();
    expect(screen.getByText("요청과 다를 수 있습니다. 그래도 진행할까요?")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "그래도 승인" }));
    expect(onDecide).toHaveBeenCalledTimes(1);
    expect(onDecide).toHaveBeenCalledWith({ approve: true, confirmMismatch: true });
  });

  it("한 번 더 묻는 곳에서 취소하면 보내지 않고 원래 버튼으로 돌아간다", () => {
    const onDecide = vi.fn(neverResolves);
    render(
      <ApprovalCard
        approval={makeApproval({ needs_mismatch_confirmation: true })}
        onDecide={onDecide}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "승인" }));
    fireEvent.click(screen.getByRole("button", { name: "취소" }));

    expect(onDecide).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "승인" })).toBeEnabled();
  });

  it("승인을 누르면 두 버튼을 바로 막고 진행 표시를 하며, 연속으로 눌러도 한 번만 보낸다", () => {
    const onDecide = vi.fn(neverResolves);
    render(<ApprovalCard approval={makeApproval()} onDecide={onDecide} />);
    const approve = screen.getByRole("button", { name: "승인" });

    fireEvent.click(approve);
    fireEvent.click(approve);

    expect(onDecide).toHaveBeenCalledTimes(1);
    expect(onDecide).toHaveBeenCalledWith({ approve: true, confirmMismatch: false });
    expect(approve).toBeDisabled();
    expect(screen.getByRole("button", { name: "거절" })).toBeDisabled();
    expect(screen.getByRole("status")).toHaveTextContent("승인 처리 중");
  });

  it("거절을 누르면 approve=false로 보낸다(mismatch여도 묻지 않는다)", () => {
    const onDecide = vi.fn(neverResolves);
    render(
      <ApprovalCard
        approval={makeApproval({ needs_mismatch_confirmation: true })}
        onDecide={onDecide}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "거절" }));
    expect(onDecide).toHaveBeenCalledWith({ approve: false, confirmMismatch: false });
    expect(screen.getByRole("status")).toHaveTextContent("거절 처리 중");
  });

  it("결정이 끝나면 버튼을 없애고 결과를 보여 준다", async () => {
    const onDecide = vi.fn().mockResolvedValue("approved");
    render(<ApprovalCard approval={makeApproval()} onDecide={onDecide} />);

    fireEvent.click(screen.getByRole("button", { name: "승인" }));

    await waitFor(() => expect(screen.getByText("승인함")).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: "승인" })).not.toBeInTheDocument();
  });

  it.each([
    ["approved", "승인함"],
    ["rejected", "거절함"],
    ["expired", "만료됨"],
  ] as const)("상태가 %s면 '%s'로 남기고 버튼이 없다", (status, text) => {
    render(<ApprovalCard approval={makeApproval({ status })} onDecide={vi.fn()} />);

    expect(screen.getByText(text)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "승인" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "거절" })).not.toBeInTheDocument();
    expect(screen.queryByText(/남음/)).not.toBeInTheDocument();
  });

  it("오류가 나면 한국어 문구를 보여 주고 다시 누를 수 있게 한다", async () => {
    const onDecide = vi
      .fn()
      .mockRejectedValue(
        new ApprovalActionError("network", "서버에 연결하지 못했습니다. 네트워크를 확인해 주세요."),
      );
    render(<ApprovalCard approval={makeApproval()} onDecide={onDecide} />);

    fireEvent.click(screen.getByRole("button", { name: "승인" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("서버에 연결하지 못했습니다");
    expect(screen.getByRole("button", { name: "승인" })).toBeEnabled();
  });

  it("서버가 mismatch 확인을 요구하면(409) 한 번 더 묻는 곳을 연다", async () => {
    const onDecide = vi
      .fn()
      .mockRejectedValue(
        new ApprovalActionError(
          "mismatch_confirmation_required",
          "요청과 다를 수 있습니다. 그래도 진행할지 한 번 더 확인해 주세요.",
        ),
      );
    render(<ApprovalCard approval={makeApproval()} onDecide={onDecide} />);

    fireEvent.click(screen.getByRole("button", { name: "승인" }));

    expect(await screen.findByRole("button", { name: "그래도 승인" })).toBeEnabled();
  });
});
