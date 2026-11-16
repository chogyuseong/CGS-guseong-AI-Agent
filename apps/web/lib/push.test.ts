import { describe, expect, it, vi } from "vitest";
import {
  decidePushState,
  decodeBase64Url,
  disablePush,
  enablePush,
  PUSH_STATE_LABELS,
  type PushBrowser,
  type PushConfig,
  PushError,
  type PushServer,
  type PushSnapshot,
  type PushSubscribeRequest,
  type PushSubscriptionLike,
  readPushStatus,
  subscriptionBody,
} from "./push";

// 서버의 VAPID 공개 키 대신 쓰는 짧은 값. base64url "AQID" = 바이트 [1, 2, 3].
const PUBLIC_KEY = "AQID";
const ENDPOINT = "https://fcm.googleapis.com/fcm/send/abc";

function fakeSubscription(key: number[] | null = [1, 2, 3], endpoint = ENDPOINT) {
  return {
    endpoint,
    options: { applicationServerKey: key === null ? null : new Uint8Array(key).buffer },
    toJSON: () => ({ endpoint, expirationTime: null, keys: { p256dh: "p256", auth: "auth" } }),
    unsubscribe: vi.fn(async () => true),
  } satisfies PushSubscriptionLike;
}

type FakeOptions = {
  permission?: NotificationPermission;
  answer?: NotificationPermission;
  existing?: PushSubscriptionLike | null;
  created?: PushSubscriptionLike;
  supported?: boolean;
  secureContext?: boolean;
};

// 브라우저 쪽(권한, 서비스 워커의 pushManager)을 흉내 낸다.
function fakeBrowser(options: FakeOptions = {}) {
  let current = options.existing ?? null;
  const created = options.created ?? fakeSubscription();
  const pushManager = {
    getSubscription: vi.fn(async () => current),
    subscribe: vi.fn(async () => {
      current = created;
      return created;
    }),
  };
  const browser: PushBrowser = {
    secureContext: options.secureContext ?? true,
    supported: options.supported ?? true,
    permission: () => options.permission ?? "default",
    requestPermission: vi.fn(async () => options.answer ?? "granted"),
    existingRegistration: vi.fn(async () => ({ pushManager })),
    readyRegistration: vi.fn(async () => ({ pushManager })),
  };
  return { browser, pushManager, created };
}

// 서버 API(/api/push/*)를 흉내 낸다.
function fakeServer(enabled = true) {
  return {
    config: vi.fn(
      async (): Promise<PushConfig> => ({ enabled, public_key: enabled ? PUBLIC_KEY : null }),
    ),
    subscribe: vi.fn(async (_body: PushSubscribeRequest) => {}),
    unsubscribe: vi.fn(async (_endpoint: string) => {}),
  } satisfies PushServer;
}

const snapshot = (overrides: Partial<PushSnapshot>): PushSnapshot => ({
  secureContext: true,
  supported: true,
  serverEnabled: true,
  permission: "default",
  subscribed: false,
  ...overrides,
});

describe("알림 상태 판정", () => {
  it("https가 아니면 insecure, 브라우저가 못 하면 unsupported(둘 다 '지원 안 함')", () => {
    expect(decidePushState(snapshot({ secureContext: false, supported: false }))).toBe("insecure");
    expect(decidePushState(snapshot({ supported: false }))).toBe("unsupported");
    expect(PUSH_STATE_LABELS.insecure).toBe("지원 안 함");
    expect(PUSH_STATE_LABELS.unsupported).toBe("지원 안 함");
  });

  it("서버에 VAPID 키가 없으면(enabled=false) server_disabled", () => {
    expect(decidePushState(snapshot({ serverEnabled: false, permission: "granted" }))).toBe(
      "server_disabled",
    );
    expect(PUSH_STATE_LABELS.server_disabled).toBe("서버에 키 없음");
  });

  it("권한이 거부됐으면 denied, 허용 + 구독이 있으면 on, 나머지는 off", () => {
    expect(decidePushState(snapshot({ permission: "denied", subscribed: true }))).toBe("denied");
    expect(decidePushState(snapshot({ permission: "granted", subscribed: true }))).toBe("on");
    expect(decidePushState(snapshot({ permission: "granted", subscribed: false }))).toBe("off");
    expect(decidePushState(snapshot({ permission: "default", subscribed: true }))).toBe("off");
    expect(PUSH_STATE_LABELS.denied).toBe("권한 거부됨");
    expect(PUSH_STATE_LABELS.on).toBe("켜짐");
    expect(PUSH_STATE_LABELS.off).toBe("꺼짐");
  });

  it("지원하지 않는 브라우저에서는 서버에 묻지 않는다", async () => {
    const server = fakeServer();
    const { browser } = fakeBrowser({ supported: false });
    expect(await readPushStatus(browser, server)).toEqual({
      state: "unsupported",
      publicKey: null,
    });
    expect(server.config).not.toHaveBeenCalled();
  });

  it("이 기기의 구독이 지금 서버 키로 만든 것이면 on, 다른 키로 만든 것이면 off", async () => {
    const server = fakeServer();
    const same = fakeBrowser({ permission: "granted", existing: fakeSubscription([1, 2, 3]) });
    expect(await readPushStatus(same.browser, server)).toEqual({
      state: "on",
      publicKey: PUBLIC_KEY,
    });
    const other = fakeBrowser({ permission: "granted", existing: fakeSubscription([9, 9]) });
    expect((await readPushStatus(other.browser, server)).state).toBe("off");
  });
});

describe("알림 켜기", () => {
  it("권한을 먼저 묻고, 허용되면 서버 키로 구독해 toJSON() 그대로 서버에 보낸다", async () => {
    const server = fakeServer();
    const { browser, pushManager } = fakeBrowser();

    expect(await enablePush(browser, server, PUBLIC_KEY)).toBe("on");

    expect(browser.requestPermission).toHaveBeenCalledTimes(1);
    const options = pushManager.subscribe.mock.calls[0] as unknown as [PushSubscriptionOptionsInit];
    expect(options[0].userVisibleOnly).toBe(true);
    expect(Array.from(options[0].applicationServerKey as Uint8Array)).toEqual([1, 2, 3]);
    expect(server.subscribe).toHaveBeenCalledWith({
      endpoint: ENDPOINT,
      expirationTime: null,
      keys: { p256dh: "p256", auth: "auth" },
    });
  });

  it("권한을 거부하면 구독하지 않고 denied, 창을 닫아 정하지 않으면 off", async () => {
    const server = fakeServer();
    const denied = fakeBrowser({ answer: "denied" });
    expect(await enablePush(denied.browser, server, PUBLIC_KEY)).toBe("denied");
    expect(denied.pushManager.subscribe).not.toHaveBeenCalled();

    const dismissed = fakeBrowser({ answer: "default" });
    expect(await enablePush(dismissed.browser, server, PUBLIC_KEY)).toBe("off");
    expect(server.subscribe).not.toHaveBeenCalled();
  });

  it("서버가 구독을 받지 않으면 브라우저 구독도 되돌리고 한국어 오류를 던진다", async () => {
    const server = fakeServer();
    server.subscribe.mockRejectedValue(new PushError("서버에 알림 구독을 저장하지 못했습니다."));
    const { browser, created } = fakeBrowser();

    await expect(enablePush(browser, server, PUBLIC_KEY)).rejects.toThrow("저장하지 못했습니다");
    expect(created.unsubscribe).toHaveBeenCalledTimes(1);
  });

  it("다른 서버 키로 만든 예전 구독이 있으면 해지하고 새로 구독한다", async () => {
    const server = fakeServer();
    const old = fakeSubscription([7, 7, 7], "https://fcm.googleapis.com/fcm/send/old");
    const { browser, pushManager } = fakeBrowser({ permission: "granted", existing: old });

    expect(await enablePush(browser, server, PUBLIC_KEY)).toBe("on");
    expect(old.unsubscribe).toHaveBeenCalledTimes(1);
    expect(pushManager.subscribe).toHaveBeenCalledTimes(1);
  });

  it("브라우저 푸시 서비스 등록이 실패하면 한국어 오류를 던지고 서버에 보내지 않는다", async () => {
    const server = fakeServer();
    const { browser, pushManager } = fakeBrowser();
    pushManager.subscribe.mockRejectedValue(new DOMException("push service error", "AbortError"));

    await expect(enablePush(browser, server, PUBLIC_KEY)).rejects.toThrow(
      "푸시 서비스에 등록하지 못했습니다",
    );
    expect(server.subscribe).not.toHaveBeenCalled();
  });
});

describe("알림 끄기", () => {
  it("서버에서 endpoint를 지우고 브라우저 구독도 해지한다", async () => {
    const server = fakeServer();
    const existing = fakeSubscription();
    const { browser } = fakeBrowser({ permission: "granted", existing });

    expect(await disablePush(browser, server)).toBe("off");
    expect(server.unsubscribe).toHaveBeenCalledWith(ENDPOINT);
    expect(existing.unsubscribe).toHaveBeenCalledTimes(1);
  });

  it("서버 요청이 실패해도 브라우저 구독은 해지한다(이 기기로는 더 오지 않는다)", async () => {
    const server = fakeServer();
    server.unsubscribe.mockRejectedValue(new PushError("연결 실패"));
    const existing = fakeSubscription();
    const { browser } = fakeBrowser({ permission: "granted", existing });

    expect(await disablePush(browser, server)).toBe("off");
    expect(existing.unsubscribe).toHaveBeenCalledTimes(1);
  });
});

describe("키와 구독 본문", () => {
  it("base64url 문자열을 바이트로 바꾼다(패딩 없음, -와 _ 포함)", () => {
    expect(Array.from(decodeBase64Url("AQID"))).toEqual([1, 2, 3]);
    expect(Array.from(decodeBase64Url("-_8"))).toEqual([0xfb, 0xff]);
  });

  it("endpoint나 키가 빠진 구독은 서버에 보내지 않는다", () => {
    expect(() => subscriptionBody({ endpoint: ENDPOINT })).toThrow(PushError);
    expect(() => subscriptionBody({ keys: { p256dh: "p", auth: "a" } })).toThrow(PushError);
  });
});
