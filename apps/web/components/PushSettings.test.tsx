import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { PushState, PushStatus } from "@/lib/push";
import { PushSettings, PushSettingsView } from "./PushSettings";

// 실제 브라우저 기능(권한, 서비스 워커)은 jsdom에 없으므로 lib/push의 세 함수만 가짜로 바꾼다.
// 상태 이름·문구 같은 나머지는 실제 코드를 쓴다.
const readMock = vi.fn<() => Promise<PushStatus>>();
const enableMock = vi.fn<(publicKey: string) => Promise<PushState>>();
const disableMock = vi.fn<() => Promise<PushState>>();
vi.mock("@/lib/push", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/push")>()),
  browserPush: () => ({}),
  readPushStatus: () => readMock(),
  enablePush: (_browser: unknown, _server: unknown, key: string) => enableMock(key),
  disablePush: () => disableMock(),
}));

const noop = () => {};
const status = (state: PushState): PushStatus => ({ state, publicKey: "AQID" });

function renderView(props: Partial<Parameters<typeof PushSettingsView>[0]> = {}) {
  return render(
    <PushSettingsView
      status={status("off")}
      loadError={false}
      busy={false}
      error={null}
      onEnable={noop}
      onDisable={noop}
      {...props}
    />,
  );
}

describe("알림 설정 화면(상태별 표시)", () => {
  it("상태를 확인하는 동안에는 확인 중이라고 보여 준다", () => {
    renderView({ status: undefined });
    expect(screen.getByText("알림 상태를 확인하는 중…")).toBeInTheDocument();
  });

  it("지원 안 함: 아이폰은 홈 화면에 추가한 뒤에만 된다고 안내하고 버튼이 없다", () => {
    renderView({ status: status("unsupported") });
    expect(screen.getByText("지원 안 함")).toBeInTheDocument();
    expect(screen.getByText(/홈 화면에 추가/)).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("https가 아니면 https 주소에서만 된다고 알리고 아이폰 안내도 보여 준다", () => {
    renderView({ status: status("insecure") });
    expect(screen.getByText("지원 안 함")).toBeInTheDocument();
    expect(screen.getByText(/https/)).toBeInTheDocument();
    expect(screen.getByText(/홈 화면에 추가/)).toBeInTheDocument();
  });

  it("서버에 키 없음: 알림 켜기 버튼을 막는다", () => {
    renderView({ status: { state: "server_disabled", publicKey: null } });
    expect(screen.getByText("서버에 키 없음")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "알림 켜기" })).toBeDisabled();
  });

  it("권한 거부됨: 브라우저 설정에서 허용하라고 안내하고 켜기 버튼을 숨긴다", () => {
    renderView({ status: status("denied") });
    expect(screen.getByText("권한 거부됨")).toBeInTheDocument();
    expect(screen.getByText(/설정에서 이 사이트의 알림을 허용/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "알림 켜기" })).not.toBeInTheDocument();
  });

  it("꺼짐이면 '알림 켜기', 켜짐이면 '알림 끄기'를 누를 수 있다", () => {
    const onEnable = vi.fn();
    const onDisable = vi.fn();
    const { rerender } = renderView({ status: status("off"), onEnable, onDisable });
    expect(screen.getByText("꺼짐")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "알림 켜기" }));
    expect(onEnable).toHaveBeenCalledTimes(1);

    rerender(
      <PushSettingsView
        status={status("on")}
        loadError={false}
        busy={false}
        error={null}
        onEnable={onEnable}
        onDisable={onDisable}
      />,
    );
    expect(screen.getByText("켜짐")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "알림 끄기" }));
    expect(onDisable).toHaveBeenCalledTimes(1);
  });

  it("처리 중에는 버튼을 막고, 오류는 한국어로 보여 준다", () => {
    renderView({ busy: true, error: "푸시 서비스에 등록하지 못했습니다." });
    expect(screen.getByRole("button", { name: "알림 켜기" })).toBeDisabled();
    expect(screen.getByRole("alert")).toHaveTextContent("푸시 서비스에 등록하지 못했습니다.");
  });
});

describe("알림 켜기·끄기 흐름", () => {
  beforeEach(() => {
    readMock.mockReset();
    enableMock.mockReset();
    disableMock.mockReset();
  });

  function renderSettings() {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    return render(
      <QueryClientProvider client={client}>
        <PushSettings />
      </QueryClientProvider>,
    );
  }

  it("알림 켜기를 누르면 서버 공개 키로 켜고, 상태를 다시 읽어 '켜짐'으로 바꾼다", async () => {
    readMock.mockResolvedValueOnce(status("off")).mockResolvedValue(status("on"));
    enableMock.mockResolvedValue("on");
    renderSettings();

    fireEvent.click(await screen.findByRole("button", { name: "알림 켜기" }));

    expect(await screen.findByText("켜짐")).toBeInTheDocument();
    expect(enableMock).toHaveBeenCalledWith("AQID");
    expect(screen.getByRole("button", { name: "알림 끄기" })).toBeEnabled();
  });

  it("연속으로 눌러도 한 번만 켠다", async () => {
    readMock.mockResolvedValue(status("off"));
    enableMock.mockImplementation(() => new Promise<PushState>(() => {}));
    renderSettings();

    const button = await screen.findByRole("button", { name: "알림 켜기" });
    fireEvent.click(button);
    fireEvent.click(button);
    expect(enableMock).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(button).toBeDisabled());
  });

  it("켜기가 실패하면 오류 문구를 보여 주고 다시 누를 수 있다", async () => {
    readMock.mockResolvedValue(status("off"));
    enableMock.mockRejectedValue(new Error("푸시 서비스에 등록하지 못했습니다."));
    renderSettings();

    fireEvent.click(await screen.findByRole("button", { name: "알림 켜기" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("등록하지 못했습니다");
    expect(screen.getByRole("button", { name: "알림 켜기" })).toBeEnabled();
  });

  it("알림 끄기를 누르면 끄고 '꺼짐'으로 바꾼다", async () => {
    readMock.mockResolvedValueOnce(status("on")).mockResolvedValue(status("off"));
    disableMock.mockResolvedValue("off");
    renderSettings();

    fireEvent.click(await screen.findByRole("button", { name: "알림 끄기" }));

    expect(await screen.findByText("꺼짐")).toBeInTheDocument();
    expect(disableMock).toHaveBeenCalledTimes(1);
  });
});
