import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { Connection } from "@/lib/connections";
import { GoogleConnectionView } from "./GoogleConnection";

function makeConnection(overrides: Partial<Connection> = {}): Connection {
  return {
    provider: "google",
    available: true,
    connected: false,
    calendar: false,
    mail: false,
    scopes: [],
    connected_at: null,
    ...overrides,
  };
}

const CONNECTED = makeConnection({
  connected: true,
  calendar: true,
  scopes: ["https://www.googleapis.com/auth/calendar.events.owned"],
  connected_at: "2026-11-16T16:30:00Z",
});

const neverResolves = () => new Promise<never>(() => {});

function renderView(
  connection: Connection | undefined,
  onDisconnect = vi.fn(neverResolves),
  onStart = vi.fn(neverResolves),
) {
  render(
    <GoogleConnectionView
      connection={connection}
      loadError={false}
      onDisconnect={onDisconnect}
      onStart={onStart}
    />,
  );
  return { region: screen.getByRole("region", { name: "Google 계정" }), onDisconnect, onStart };
}

describe("Google 연결 영역(상태별 표시)", () => {
  it("확인 중이면 확인 중이라고, 읽지 못하면 다시 열어 달라고 보여 준다", () => {
    renderView(undefined);
    expect(screen.getByText("연결 상태를 확인하는 중…")).toBeInTheDocument();
  });

  it("상태를 읽지 못하면 안내하고 연결 버튼을 보여 주지 않는다", () => {
    render(<GoogleConnectionView connection={undefined} loadError onDisconnect={vi.fn()} />);
    expect(screen.getByText(/연결 상태를 확인하지 못했습니다/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Google 계정 연결" })).not.toBeInTheDocument();
  });

  it("연결 안 됨: 요청 권한과 승인 안내, 누르면 캘린더만 요청하는 '연결' 버튼을 보여 준다", () => {
    const { region, onStart } = renderView(makeConnection());
    expect(within(region).getByText("연결 안 됨")).toBeInTheDocument();
    expect(region).toHaveTextContent("내 캘린더의 일정 보기·만들기·고치기·지우기");
    expect(region).toHaveTextContent("메일은 요청하지 않습니다");
    expect(region).toHaveTextContent("만들기·고치기·지우기 전에는 매번 승인을 묻습니다");
    fireEvent.click(within(region).getByRole("button", { name: "Google 계정 연결" }));
    expect(onStart).toHaveBeenCalledWith(undefined);
    expect(within(region).queryByRole("button", { name: "연결 해제" })).not.toBeInTheDocument();
  });

  it("연결됨: 캘린더 권한, 연결한 날짜, 다시 연결 버튼과 연결 해제 버튼을 보여 준다", () => {
    const { region } = renderView(CONNECTED);
    expect(within(region).getByText("연결됨")).toBeInTheDocument();
    expect(region).toHaveTextContent("캘린더 권한있음");
    expect(region).toHaveTextContent("2026년 11월 17일");
    expect(within(region).getByRole("button", { name: "다시 연결" })).toBeEnabled();
    expect(within(region).getByRole("button", { name: "연결 해제" })).toBeEnabled();
  });

  it("연결됐지만 캘린더 권한이 없으면 다시 연결하라고 알린다", () => {
    const { region } = renderView(makeConnection({ connected: true, calendar: false }));
    expect(region).toHaveTextContent("캘린더 권한없음");
    expect(region).toHaveTextContent("캘린더 권한이 없어 일정 도구를 쓸 수 없습니다");
  });

  it("서버 설정이 없으면 '서버 설정 없음'을 보여 주고 연결 버튼을 막는다", () => {
    const { region } = renderView(makeConnection({ available: false }));
    expect(within(region).getByText("서버 설정 없음")).toBeInTheDocument();
    expect(region).toHaveTextContent(".env");
    expect(within(region).getByRole("button", { name: "Google 계정 연결" })).toBeDisabled();
  });
});

function mailRegion() {
  return screen.getByRole("region", { name: "메일 읽기 권한" });
}

describe("메일 읽기 권한(7단계)", () => {
  it("연결됐지만 메일 권한이 없으면 '없음'과 scope=mail 추가 버튼을 보여 준다. 기본 연결은 캘린더만", () => {
    const { onStart } = renderView(CONNECTED);
    const mail = mailRegion();
    expect(within(mail).getByText("없음")).toBeInTheDocument();
    fireEvent.click(within(mail).getByRole("button", { name: "메일 읽기 권한 추가" }));
    expect(onStart).toHaveBeenLastCalledWith("mail");
    // [다시 연결]은 메일을 요청하지 않는다.
    fireEvent.click(screen.getByRole("button", { name: "다시 연결" }));
    expect(onStart).toHaveBeenLastCalledWith(undefined);
  });

  it("무엇을 하는지 안내한다(읽기만, 4,000자·첨부 이름, 자동 작업 제외, 확인되지 않은 앱 경고)", () => {
    renderView(CONNECTED);
    const mail = mailRegion();
    expect(mail).toHaveTextContent(
      "메일을 읽기만 합니다. 보내기·답장·삭제·라벨 변경은 하지 않습니다",
    );
    expect(mail).toHaveTextContent("본문은 4,000자까지만 읽고 첨부는 이름만 봅니다");
    expect(mail).toHaveTextContent("예약 작업과 조건 감시는 메일을 읽지 않습니다");
    expect(mail).toHaveTextContent("Google 동의 화면에 '확인되지 않은 앱' 경고가 뜰 수 있습니다");
  });

  it("연결 전이어도 추가 버튼을 보여 주고, 캘린더와 함께 연결된다고 알린다", () => {
    renderView(makeConnection());
    const mail = mailRegion();
    expect(within(mail).getByText("없음")).toBeInTheDocument();
    expect(within(mail).getByRole("button", { name: "메일 읽기 권한 추가" })).toBeEnabled();
    expect(mail).toHaveTextContent("캘린더 권한과 함께 요청해 연결합니다");
  });

  it("메일 권한이 있으면 '있음'을 보여 주고 추가 버튼 대신 빼는 방법을 안내한다", () => {
    renderView({ ...CONNECTED, mail: true });
    const mail = mailRegion();
    expect(within(mail).getByText("있음")).toBeInTheDocument();
    expect(
      within(mail).queryByRole("button", { name: "메일 읽기 권한 추가" }),
    ).not.toBeInTheDocument();
    expect(mail).toHaveTextContent("메일 권한만 끄는 방법은 없습니다");
    expect(mail).toHaveTextContent("연결 해제 후 캘린더만 다시 연결");
    expect(mail).toHaveTextContent("[다시 연결]을 눌러도 메일 권한은 남습니다");
  });

  it("서버 설정이 없으면 추가 버튼을 막는다", () => {
    renderView(makeConnection({ available: false }));
    const mail = mailRegion();
    expect(within(mail).getByRole("button", { name: "메일 읽기 권한 추가" })).toBeDisabled();
  });

  it("연결 상태를 아직 모르면 메일 영역을 그리지 않는다", () => {
    renderView(undefined);
    expect(screen.queryByRole("region", { name: "메일 읽기 권한" })).not.toBeInTheDocument();
  });
});

describe("연결 해제 확인 흐름", () => {
  it("연결 해제를 누르면 바로 해제하지 않고 화면 안에서 한 번 더 묻는다. 취소하면 돌아간다", () => {
    const { onDisconnect } = renderView(CONNECTED);
    fireEvent.click(screen.getByRole("button", { name: "연결 해제" }));

    expect(onDisconnect).not.toHaveBeenCalled();
    expect(screen.getByText("Google 연결을 해제할까요?")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "취소" }));

    expect(screen.queryByText("Google 연결을 해제할까요?")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "연결 해제" })).toBeEnabled();
    expect(onDisconnect).not.toHaveBeenCalled();
  });

  it("확인에서 '해제하기'를 누르면 한 번만 보내고 처리 중에는 버튼을 막는다", () => {
    const { onDisconnect } = renderView(CONNECTED);
    fireEvent.click(screen.getByRole("button", { name: "연결 해제" }));
    const confirm = screen.getByRole("button", { name: "해제하기" });

    fireEvent.click(confirm);
    fireEvent.click(confirm);

    expect(onDisconnect).toHaveBeenCalledTimes(1);
    expect(confirm).toBeDisabled();
    expect(screen.getByRole("button", { name: "취소" })).toBeDisabled();
    expect(screen.getByRole("status")).toHaveTextContent("해제하는 중");
  });

  it("해제가 끝나면 완료 안내를 보여 준다", async () => {
    const onDisconnect = vi.fn().mockResolvedValue(undefined);
    renderView(CONNECTED, onDisconnect);
    fireEvent.click(screen.getByRole("button", { name: "연결 해제" }));
    fireEvent.click(screen.getByRole("button", { name: "해제하기" }));

    expect(await screen.findByText("Google 연결을 해제했습니다.")).toBeInTheDocument();
  });

  it("해제가 실패하면 한국어 오류를 보여 주고 다시 누를 수 있다", async () => {
    const onDisconnect = vi.fn().mockRejectedValue(new Error("연결을 해제하지 못했습니다."));
    renderView(CONNECTED, onDisconnect);
    fireEvent.click(screen.getByRole("button", { name: "연결 해제" }));
    fireEvent.click(screen.getByRole("button", { name: "해제하기" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("연결을 해제하지 못했습니다.");
    expect(screen.getByRole("button", { name: "해제하기" })).toBeEnabled();
  });
});

describe("연결 시작 버튼(4단계 보강: POST)", () => {
  it("누르면 한 번만 시작하고, 이동하는 동안 버튼을 막는다", () => {
    const { region, onStart } = renderView(makeConnection());
    const button = within(region).getByRole("button", { name: "Google 계정 연결" });
    fireEvent.click(button);
    fireEvent.click(button);
    expect(onStart).toHaveBeenCalledTimes(1);
    expect(button).toBeDisabled();
    expect(region).toHaveTextContent("Google 동의 화면으로 이동하는 중");
  });

  it("시작이 실패하면 한국어 오류를 보여 주고 다시 누를 수 있다", async () => {
    const onStart = vi
      .fn()
      .mockRejectedValue(new Error("서버에 Google 연결 설정이 없어 연결할 수 없습니다."));
    const { region } = renderView(makeConnection(), vi.fn(neverResolves), onStart);
    const button = within(region).getByRole("button", { name: "Google 계정 연결" });
    fireEvent.click(button);
    expect(await within(region).findByRole("alert")).toHaveTextContent(
      "서버에 Google 연결 설정이 없어",
    );
    expect(button).toBeEnabled();
  });

  it("해제 확인에서 기록은 지워지지 않고 대기 중인 일정 작업은 실행되지 않는다고 알린다", () => {
    const { region } = renderView(CONNECTED);
    fireEvent.click(within(region).getByRole("button", { name: "연결 해제" }));
    expect(region).toHaveTextContent("승인을 기다리던 일정 작업은 실행되지 않습니다");
    expect(region).toHaveTextContent("연결 해제는 기록 삭제와 다릅니다");
  });
});
