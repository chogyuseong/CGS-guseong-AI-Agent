// public/sw.js(서비스 워커)를 가짜 self 위에서 실제로 실행해 push·notificationclick 처리를 확인한다.
// 서비스 워커는 페이지와 다른 전역(self)에서 돌므로 node:vm으로 따로 실행한다.
import { readFileSync } from "node:fs";
import path from "node:path";
import { runInNewContext } from "node:vm";
import { describe, expect, it, vi } from "vitest";

const ORIGIN = "https://app.example";
const SOURCE = readFileSync(path.join(process.cwd(), "public", "sw.js"), "utf8");

type Listener = (event: unknown) => void;
type FakeClient = {
  url: string;
  focus: ReturnType<typeof vi.fn>;
  navigate: ReturnType<typeof vi.fn>;
};

function loadWorker(windows: FakeClient[] = []) {
  const listeners = new Map<string, Listener>();
  const self = {
    location: { origin: ORIGIN },
    registration: { showNotification: vi.fn(async () => {}) },
    clients: {
      matchAll: vi.fn(async () => windows),
      openWindow: vi.fn(async () => null),
      claim: vi.fn(async () => {}),
    },
    skipWaiting: vi.fn(async () => {}),
    addEventListener: (type: string, listener: Listener) => listeners.set(type, listener),
  };
  runInNewContext(SOURCE, { self, URL });
  // 이벤트를 보내고 event.waitUntil에 넘긴 작업이 끝날 때까지 기다린다.
  async function dispatch(type: string, event: Record<string, unknown>) {
    const waits: Promise<unknown>[] = [];
    listeners.get(type)?.({ ...event, waitUntil: (work: Promise<unknown>) => waits.push(work) });
    await Promise.all(waits);
  }
  return { self, listeners, dispatch };
}

const pushEvent = (payload: unknown) => ({ data: { json: () => payload } });

function clickEvent(url: unknown) {
  return { notification: { data: { url }, close: vi.fn() } };
}

function fakeWindow(url: string): FakeClient {
  return { url, focus: vi.fn(async () => {}), navigate: vi.fn(async () => {}) };
}

describe("sw.js push", () => {
  it("payload의 title·body로 알림을 띄우고 url을 알림 데이터에 넣는다", async () => {
    const { self, dispatch } = loadWorker();
    await dispatch(
      "push",
      pushEvent({
        title: "마이에이전트",
        body: "승인이 필요한 작업이 있습니다",
        url: "/chat/7",
        kind: "approval_needed",
      }),
    );

    expect(self.registration.showNotification).toHaveBeenCalledWith(
      "마이에이전트",
      expect.objectContaining({ body: "승인이 필요한 작업이 있습니다", data: { url: "/chat/7" } }),
    );
  });

  it.each([
    "https://evil.example/chat/7",
    "//evil.example/chat",
    "/\\evil.example/chat",
    "javascript:alert(1)",
    "chat/7",
    42,
  ])("같은 출처의 경로가 아닌 url(%s)은 /chat으로 바꾼다", async (url) => {
    const { self, dispatch } = loadWorker();
    await dispatch("push", pushEvent({ title: "마이에이전트", body: "알림", url }));

    expect(self.registration.showNotification).toHaveBeenCalledWith(
      "마이에이전트",
      expect.objectContaining({ data: { url: "/chat" } }),
    );
  });

  it("payload가 JSON이 아니거나 글자가 아니면 기본 문구로 알림을 띄운다", async () => {
    const { self, dispatch } = loadWorker();
    await dispatch("push", {
      data: {
        json: () => {
          throw new SyntaxError("bad json");
        },
      },
    });
    await dispatch("push", pushEvent({ title: { x: 1 }, body: 3 }));

    for (const call of self.registration.showNotification.mock.calls as unknown as [
      string,
      { body: string; data: { url: string } },
    ][]) {
      expect(call[0]).toBe("마이에이전트");
      expect(call[1].body).toBe("새 알림이 있습니다.");
      expect(call[1].data.url).toBe("/chat");
    }
    expect(self.registration.showNotification).toHaveBeenCalledTimes(2);
  });

  it("캐싱·오프라인 처리(fetch 이벤트)는 하지 않는다", () => {
    const { listeners } = loadWorker();
    expect(listeners.has("fetch")).toBe(false);
  });
});

describe("sw.js notificationclick", () => {
  it("이미 열린 창이 있으면 그 창을 포커스하고 url로 이동한다(새 창을 열지 않는다)", async () => {
    const open = fakeWindow(`${ORIGIN}/settings`);
    const { self, dispatch } = loadWorker([open]);
    const event = clickEvent("/chat/7");
    await dispatch("notificationclick", event);

    expect(event.notification.close).toHaveBeenCalled();
    expect(open.focus).toHaveBeenCalled();
    expect(open.navigate).toHaveBeenCalledWith(`${ORIGIN}/chat/7`);
    expect(self.clients.openWindow).not.toHaveBeenCalled();
  });

  it("이미 그 주소를 보고 있으면 포커스만 한다", async () => {
    const open = fakeWindow(`${ORIGIN}/chat/7`);
    const { dispatch } = loadWorker([open]);
    await dispatch("notificationclick", clickEvent("/chat/7"));

    expect(open.focus).toHaveBeenCalled();
    expect(open.navigate).not.toHaveBeenCalled();
  });

  it("열린 창이 없으면 새 창으로 연다", async () => {
    const { self, dispatch } = loadWorker([]);
    await dispatch("notificationclick", clickEvent("/chat/7"));
    expect(self.clients.openWindow).toHaveBeenCalledWith(`${ORIGIN}/chat/7`);
  });

  it("다른 출처의 창은 쓰지 않고, 알림 데이터의 url도 다시 검사한다", async () => {
    const foreign = fakeWindow("https://other.example/");
    const { self, dispatch } = loadWorker([foreign]);
    await dispatch("notificationclick", clickEvent("https://evil.example/"));

    expect(foreign.focus).not.toHaveBeenCalled();
    expect(self.clients.openWindow).toHaveBeenCalledWith(`${ORIGIN}/chat`);
  });

  it("창 이동이 실패하면(제어하지 않는 창) 새 창으로 연다", async () => {
    const open = fakeWindow(`${ORIGIN}/settings`);
    open.navigate.mockRejectedValue(new TypeError("not controlled"));
    const { self, dispatch } = loadWorker([open]);
    await dispatch("notificationclick", clickEvent("/chat/7"));

    expect(self.clients.openWindow).toHaveBeenCalledWith(`${ORIGIN}/chat/7`);
  });
});
