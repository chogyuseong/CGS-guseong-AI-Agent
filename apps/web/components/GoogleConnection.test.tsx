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

function renderView(connection: Connection | undefined, onDisconnect = vi.fn(neverResolves)) {
  render(
    <GoogleConnectionView connection={connection} loadError={false} onDisconnect={onDisconnect} />,
  );
  return { region: screen.getByRole("region", { name: "Google 계정" }), onDisconnect };
}

describe("Google 연결 영역(상태별 표시)", () => {
  it("확인 중이면 확인 중이라고, 읽지 못하면 다시 열어 달라고 보여 준다", () => {
    renderView(undefined);
    expect(screen.getByText("연결 상태를 확인하는 중…")).toBeInTheDocument();
  });

  it("상태를 읽지 못하면 안내하고 연결 버튼을 보여 주지 않는다", () => {
    render(<GoogleConnectionView connection={undefined} loadError onDisconnect={vi.fn()} />);
    expect(screen.getByText(/연결 상태를 확인하지 못했습니다/)).toBeInTheDocument();
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });

  it("연결 안 됨: 요청 권한과 승인 안내, start 주소로 가는 '연결' 링크를 보여 준다", () => {
    const { region } = renderView(makeConnection());
    expect(within(region).getByText("연결 안 됨")).toBeInTheDocument();
    expect(region).toHaveTextContent("내 캘린더의 일정 보기·만들기·고치기·지우기");
    expect(region).toHaveTextContent("메일은 요청하지 않습니다");
    expect(region).toHaveTextContent("만들기·고치기·지우기 전에는 매번 승인을 묻습니다");
    const link = within(region).getByRole("link", { name: "Google 계정 연결" });
    expect(link).toHaveAttribute("href", "/api/connections/google/start");
    expect(within(region).queryByRole("button", { name: "연결 해제" })).not.toBeInTheDocument();
  });

  it("연결됨: 캘린더 권한, 연결한 날짜, 다시 연결 링크와 연결 해제 버튼을 보여 준다", () => {
    const { region } = renderView(CONNECTED);
    expect(within(region).getByText("연결됨")).toBeInTheDocument();
    expect(region).toHaveTextContent("캘린더 권한있음");
    expect(region).toHaveTextContent("2026년 11월 17일");
    const relink = within(region).getByRole("link", { name: "다시 연결" });
    expect(relink).toHaveAttribute("href", "/api/connections/google/start");
    expect(within(region).getByRole("button", { name: "연결 해제" })).toBeEnabled();
  });

  it("연결됐지만 캘린더 권한이 없으면 다시 연결하라고 알린다", () => {
    const { region } = renderView(makeConnection({ connected: true, calendar: false }));
    expect(region).toHaveTextContent("캘린더 권한없음");
    expect(region).toHaveTextContent("캘린더 권한이 없어 일정 도구를 쓸 수 없습니다");
  });

  it("서버 설정이 없으면 '서버 설정 없음'을 보여 주고 연결 버튼을 막는다(링크 없음)", () => {
    const { region } = renderView(makeConnection({ available: false }));
    expect(within(region).getByText("서버 설정 없음")).toBeInTheDocument();
    expect(region).toHaveTextContent(".env");
    expect(within(region).queryByRole("link")).not.toBeInTheDocument();
    expect(within(region).getByRole("button", { name: "Google 계정 연결" })).toBeDisabled();
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
