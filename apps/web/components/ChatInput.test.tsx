import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ChatInput } from "./ChatInput";

describe("ChatInput", () => {
  it("전송 중에는 입력창과 전송 버튼을 막고 진행 중 표시를 보여 준다", () => {
    render(<ChatInput onSend={vi.fn()} pending />);

    expect(screen.getByLabelText("메시지 입력")).toBeDisabled();
    expect(screen.getByRole("button", { name: "전송" })).toBeDisabled();
    expect(screen.getByRole("status")).toHaveTextContent("답변을 기다리는 중");
  });

  it("제안을 고르면 입력창에 채우고 포커스만 준다. 보내지 않는다(6단계 보강)", () => {
    const onSend = vi.fn();
    const { rerender } = render(<ChatInput onSend={onSend} pending={false} prefill={null} />);

    rerender(
      <ChatInput
        onSend={onSend}
        pending={false}
        prefill={{ id: 1, text: "우유 사기 할 일 추가해줘" }}
      />,
    );

    const input = screen.getByLabelText("메시지 입력");
    expect(input).toHaveValue("우유 사기 할 일 추가해줘");
    expect(input).toHaveFocus();
    expect(onSend).not.toHaveBeenCalled();
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

// 브라우저의 SpeechRecognition을 흉내 내는 가짜. 실제 마이크·음성 인식은 쓰지 않는다.
// 진짜처럼 stop()·abort() 뒤에 end 이벤트가 온다(실제로는 조금 뒤에 오지만 테스트에서는 바로).
class FakeRecognition {
  static instances: FakeRecognition[] = [];
  lang = "";
  continuous = true;
  interimResults = true;
  maxAlternatives = 0;
  onresult: ((event: unknown) => void) | null = null;
  onerror: ((event: unknown) => void) | null = null;
  onend: (() => void) | null = null;
  start = vi.fn();
  stop = vi.fn(() => this.onend?.());
  abort = vi.fn(() => this.onend?.());

  constructor() {
    FakeRecognition.instances.push(this);
  }

  say(text: string) {
    const result = Object.assign([{ transcript: text, confidence: 0.9 }], { isFinal: true });
    act(() => this.onresult?.({ resultIndex: 0, results: [result] }));
  }

  finish() {
    act(() => this.onend?.());
  }

  fail(code: string) {
    act(() => {
      this.onerror?.({ error: code });
      this.onend?.();
    });
  }
}

function lastRecognition(): FakeRecognition {
  const recognition = FakeRecognition.instances.at(-1);
  if (!recognition) throw new Error("음성 인식이 시작되지 않았습니다");
  return recognition;
}

function micButton() {
  return screen.getByRole("button", { name: "음성 입력" });
}

describe("ChatInput 음성 입력", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    FakeRecognition.instances = [];
  });

  it("음성 인식을 지원하지 않는 브라우저에서는 마이크 버튼을 숨긴다", () => {
    vi.stubGlobal("SpeechRecognition", undefined);
    vi.stubGlobal("webkitSpeechRecognition", undefined);
    render(<ChatInput onSend={vi.fn()} pending={false} />);

    expect(screen.queryByRole("button", { name: "음성 입력" })).not.toBeInTheDocument();
  });

  it("webkit 접두사만 있어도 버튼을 보여 주고, 누르면 한국어로 듣기 시작한다", () => {
    vi.stubGlobal("webkitSpeechRecognition", FakeRecognition);
    render(<ChatInput onSend={vi.fn()} pending={false} />);

    expect(micButton()).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(micButton());

    const recognition = lastRecognition();
    expect(recognition.lang).toBe("ko-KR");
    expect(recognition.start).toHaveBeenCalledTimes(1);
    expect(micButton()).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("status")).toHaveTextContent("듣는 중");
  });

  it("인식 결과는 입력창에만 넣고 전송하지 않는다", () => {
    vi.stubGlobal("SpeechRecognition", FakeRecognition);
    const onSend = vi.fn();
    render(<ChatInput onSend={onSend} pending={false} />);

    fireEvent.click(micButton());
    lastRecognition().say("내일 서울 날씨 알려줘");
    lastRecognition().finish();

    expect(screen.getByLabelText("메시지 입력")).toHaveValue("내일 서울 날씨 알려줘");
    expect(onSend).not.toHaveBeenCalled();
    expect(micButton()).toHaveAttribute("aria-pressed", "false");
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("이미 입력한 글이 있으면 지우지 않고 한 칸 띄워 이어 붙인다", () => {
    vi.stubGlobal("SpeechRecognition", FakeRecognition);
    render(<ChatInput onSend={vi.fn()} pending={false} />);
    const input = screen.getByLabelText("메시지 입력");

    fireEvent.change(input, { target: { value: "내일 오후 3시에" } });
    fireEvent.click(micButton());
    lastRecognition().say("치과 일정 넣어줘");

    expect(input).toHaveValue("내일 오후 3시에 치과 일정 넣어줘");
  });

  it("듣는 중에 다시 누르면 멈추고, 무음 안내를 띄우지 않는다", () => {
    vi.stubGlobal("SpeechRecognition", FakeRecognition);
    render(<ChatInput onSend={vi.fn()} pending={false} />);

    fireEvent.click(micButton());
    fireEvent.click(micButton());

    expect(lastRecognition().stop).toHaveBeenCalledTimes(1);
    expect(micButton()).toHaveAttribute("aria-pressed", "false");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("마이크 권한이 거부되면 한국어로 허용 방법을 안내한다", () => {
    vi.stubGlobal("SpeechRecognition", FakeRecognition);
    vi.stubGlobal("isSecureContext", true); // https로 연 것처럼(jsdom 기본값은 false)
    render(<ChatInput onSend={vi.fn()} pending={false} />);

    fireEvent.click(micButton());
    lastRecognition().fail("not-allowed");

    expect(screen.getByRole("alert")).toHaveTextContent("마이크 권한");
    expect(micButton()).toHaveAttribute("aria-pressed", "false");
  });

  it("https가 아닌 주소에서 권한이 막히면 https 주소로 열라고 안내한다", () => {
    vi.stubGlobal("SpeechRecognition", FakeRecognition);
    vi.stubGlobal("isSecureContext", false);
    render(<ChatInput onSend={vi.fn()} pending={false} />);

    fireEvent.click(micButton());
    lastRecognition().fail("not-allowed");

    expect(screen.getByRole("alert")).toHaveTextContent("https");
  });

  it("말소리가 없으면 다시 말해 달라고 안내하고, 다시 누르면 안내를 지운다", () => {
    vi.stubGlobal("SpeechRecognition", FakeRecognition);
    render(<ChatInput onSend={vi.fn()} pending={false} />);

    fireEvent.click(micButton());
    lastRecognition().fail("no-speech");
    expect(screen.getByRole("alert")).toHaveTextContent("말소리가 들리지 않았습니다");

    fireEvent.click(micButton());
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("오류 없이 결과도 없이 끝나면 무음 안내를 띄운다", () => {
    vi.stubGlobal("SpeechRecognition", FakeRecognition);
    render(<ChatInput onSend={vi.fn()} pending={false} />);

    fireEvent.click(micButton());
    lastRecognition().finish();

    expect(screen.getByRole("alert")).toHaveTextContent("말소리가 들리지 않았습니다");
  });

  it("그 밖의 오류는 일반 안내를 띄운다", () => {
    vi.stubGlobal("SpeechRecognition", FakeRecognition);
    render(<ChatInput onSend={vi.fn()} pending={false} />);

    fireEvent.click(micButton());
    lastRecognition().fail("network");

    expect(screen.getByRole("alert")).toHaveTextContent("음성 인식");
  });

  it("시작이 실패하면 안내를 띄우고 듣는 중 상태가 되지 않는다", () => {
    class BrokenRecognition extends FakeRecognition {
      override start = vi.fn(() => {
        throw new Error("InvalidStateError");
      });
    }
    vi.stubGlobal("SpeechRecognition", BrokenRecognition);
    render(<ChatInput onSend={vi.fn()} pending={false} />);

    fireEvent.click(micButton());

    expect(micButton()).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByRole("alert")).toHaveTextContent("음성 인식");
  });

  it("전송 중에는 마이크 버튼을 막는다", () => {
    vi.stubGlobal("SpeechRecognition", FakeRecognition);
    render(<ChatInput onSend={vi.fn()} pending />);

    expect(micButton()).toBeDisabled();
  });

  it("듣는 중에 전송하면 인식을 취소해 늦게 온 결과가 입력창에 들어가지 않는다", async () => {
    vi.stubGlobal("SpeechRecognition", FakeRecognition);
    const onSend = vi.fn().mockResolvedValue(undefined);
    render(<ChatInput onSend={onSend} pending={false} />);
    const input = screen.getByLabelText("메시지 입력");

    fireEvent.change(input, { target: { value: "할 일 보여줘" } });
    fireEvent.click(micButton());
    const recognition = lastRecognition();
    fireEvent.click(screen.getByRole("button", { name: "전송" }));
    await waitFor(() => expect(input).toHaveValue(""));
    recognition.say("늦게 온 말");

    expect(recognition.abort).toHaveBeenCalled();
    expect(input).toHaveValue("");
  });

  it("듣는 중에 전송 중 상태가 되면 인식을 취소한다", () => {
    vi.stubGlobal("SpeechRecognition", FakeRecognition);
    const { rerender } = render(<ChatInput onSend={vi.fn()} pending={false} />);

    fireEvent.click(micButton());
    rerender(<ChatInput onSend={vi.fn()} pending />);

    expect(lastRecognition().abort).toHaveBeenCalled();
    expect(micButton()).toHaveAttribute("aria-pressed", "false");
  });

  it("화면을 떠나면 인식을 멈춘다", () => {
    vi.stubGlobal("SpeechRecognition", FakeRecognition);
    const { unmount } = render(<ChatInput onSend={vi.fn()} pending={false} />);

    fireEvent.click(micButton());
    unmount();

    expect(lastRecognition().abort).toHaveBeenCalled();
  });
});
