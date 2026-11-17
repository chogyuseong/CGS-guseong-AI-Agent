import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { readMessageDraft } from "@/lib/stepOutput";
import { DraftCard } from "./DraftCard";

const BODY = "민수야, 내일 저녁 7시에 성수동에서 보자.\n늦으면 연락 줘.";
const OUTPUT = {
  status: "succeeded",
  data: {
    kind: "message_draft",
    recipient: "민수",
    body: BODY,
    sent: false,
    notice: "보내지 않았습니다.",
  },
};

describe("메시지 초안(4단계 보강)", () => {
  afterEach(() => vi.restoreAllMocks());

  it("draft_message 결과만 초안으로 읽는다", () => {
    expect(readMessageDraft("draft_message", OUTPUT)).toEqual({ recipient: "민수", body: BODY });
    expect(readMessageDraft("save_note", OUTPUT)).toBeNull();
    expect(readMessageDraft("draft_message", { data: { ...OUTPUT.data, sent: true } })).toBeNull();
    expect(readMessageDraft("draft_message", { data: { kind: "other" } })).toBeNull();
  });

  it("받는 사람과 본문을 나눠 보여 주고, 보냈다고 하지 않는다", () => {
    render(<DraftCard draft={{ recipient: "민수", body: BODY }} />);
    const card = screen.getByRole("article", { name: "메시지 초안" });
    expect(card).toHaveTextContent("받는 사람민수");
    expect(card).toHaveTextContent("늦으면 연락 줘.");
    expect(card).toHaveTextContent("보내지 않았습니다");
    expect(card.textContent).not.toMatch(/보냈습니다|발송 완료|전송 완료/);
  });

  it("복사 버튼은 본문만 복사하고 복사했다고 알린다", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("navigator", { clipboard: { writeText } });
    render(<DraftCard draft={{ recipient: "민수", body: BODY }} />);

    fireEvent.click(screen.getByRole("button", { name: "본문 복사" }));

    expect(await screen.findByRole("button", { name: "복사했습니다" })).toBeInTheDocument();
    expect(writeText).toHaveBeenCalledWith(BODY);
    vi.unstubAllGlobals();
  });

  it("복사가 안 되면 직접 복사하라고 알린다", async () => {
    vi.stubGlobal("navigator", {
      clipboard: { writeText: vi.fn().mockRejectedValue(new Error("x")) },
    });
    render(<DraftCard draft={{ recipient: "민수", body: BODY }} />);

    fireEvent.click(screen.getByRole("button", { name: "본문 복사" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("직접 복사해 주세요");
    vi.unstubAllGlobals();
  });
});
