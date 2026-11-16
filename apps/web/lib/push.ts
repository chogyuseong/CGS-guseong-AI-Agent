// 웹 푸시 '알림 켜기'·끄기. 브라우저 기능(권한, 서비스 워커의 pushManager)과 서버 API(/api/push/*)를
// 인자로 받아 테스트에서 가짜로 바꿀 수 있게 했다. 타입은 pnpm gen:api가 만든 schema.d.ts에서 꺼낸다.
//
// 흐름: 권한 요청(버튼을 눌렀을 때만) → 서비스 워커 준비 → 서버 VAPID 공개 키로 구독 → subscription.toJSON()을 서버에 저장.
import { api } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";

type Schemas = components["schemas"];
export type PushConfig = Schemas["PushConfigOut"];
export type PushSubscribeRequest = Schemas["PushSubscribeRequest"];

// insecure: https(또는 localhost)가 아니라 브라우저가 푸시 기능을 숨김 / unsupported: 브라우저가 지원하지 않음
// (아이폰은 홈 화면에 추가한 앱에서만 지원) / server_disabled: 서버에 VAPID 설정이 없음(enabled=false)
export type PushState = "insecure" | "unsupported" | "server_disabled" | "denied" | "on" | "off";
export type PushStatus = { state: PushState; publicKey: string | null };

export const PUSH_STATE_LABELS: Record<PushState, string> = {
  insecure: "지원 안 함",
  unsupported: "지원 안 함",
  server_disabled: "서버에 키 없음",
  denied: "권한 거부됨",
  on: "켜짐",
  off: "꺼짐",
};

// 화면에 그대로 보여 줄 한국어 문장을 담는 오류.
export class PushError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "PushError";
  }
}

// 브라우저의 PushSubscription·PushManager 중 이 앱이 쓰는 부분만. 실제 객체도 이 모양을 만족한다.
export type PushSubscriptionLike = {
  endpoint: string;
  options: { applicationServerKey: ArrayBuffer | null };
  toJSON(): PushSubscriptionJSON;
  unsubscribe(): Promise<boolean>;
};
type PushManagerLike = {
  getSubscription(): Promise<PushSubscriptionLike | null>;
  subscribe(options: PushSubscriptionOptionsInit): Promise<PushSubscriptionLike>;
};
type PushRegistrationLike = { pushManager: PushManagerLike };

export type PushBrowser = {
  secureContext: boolean;
  supported: boolean;
  permission(): NotificationPermission;
  requestPermission(): Promise<NotificationPermission>;
  // 이미 등록된 서비스 워커(없으면 undefined). 상태를 읽을 때 쓴다.
  existingRegistration(): Promise<PushRegistrationLike | undefined>;
  // 등록하고 활성화될 때까지 기다린 서비스 워커. 구독할 때 쓴다.
  readyRegistration(): Promise<PushRegistrationLike>;
};

export type PushServer = {
  config(): Promise<PushConfig>;
  subscribe(body: PushSubscribeRequest): Promise<void>;
  unsubscribe(endpoint: string): Promise<void>;
};

export type PushSnapshot = {
  secureContext: boolean;
  supported: boolean;
  serverEnabled: boolean;
  permission: NotificationPermission;
  subscribed: boolean;
};

export function decidePushState(snapshot: PushSnapshot): PushState {
  if (!snapshot.secureContext) return "insecure";
  if (!snapshot.supported) return "unsupported";
  if (!snapshot.serverEnabled) return "server_disabled";
  if (snapshot.permission === "denied") return "denied";
  if (snapshot.permission === "granted" && snapshot.subscribed) return "on";
  return "off";
}

// VAPID 공개 키(base64url, 패딩 없음)를 applicationServerKey에 넣을 바이트로 바꾼다.
export function decodeBase64Url(value: string): Uint8Array<ArrayBuffer> {
  const padded = value.padEnd(value.length + ((4 - (value.length % 4)) % 4), "=");
  const binary = atob(padded.replace(/-/g, "+").replace(/_/g, "/"));
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
  return bytes;
}

// 이 구독이 지금 서버 키로 만든 것인지. 서버 키를 바꾸면 예전 구독으로는 푸시가 오지 않는다.
// 브라우저가 키를 알려 주지 않으면(null) 확인할 수 없으므로 맞는 것으로 본다.
function keyMatches(subscription: PushSubscriptionLike, publicKey: string): boolean {
  const key = subscription.options.applicationServerKey;
  if (key === null) return true;
  const current = new Uint8Array(key);
  const expected = decodeBase64Url(publicKey);
  return current.length === expected.length && current.every((byte, i) => byte === expected[i]);
}

// subscription.toJSON()을 서버 본문으로. endpoint와 키가 없으면 서버가 푸시를 보낼 수 없어 막는다.
export function subscriptionBody(json: PushSubscriptionJSON): PushSubscribeRequest {
  const p256dh = json.keys?.p256dh;
  const auth = json.keys?.auth;
  if (!json.endpoint || !p256dh || !auth) {
    throw new PushError("브라우저가 알림 구독 정보를 주지 않았습니다. 다시 시도해 주세요.");
  }
  return {
    endpoint: json.endpoint,
    expirationTime: json.expirationTime ?? null,
    keys: { p256dh, auth },
  };
}

export async function readPushStatus(
  browser: PushBrowser,
  server: PushServer,
): Promise<PushStatus> {
  const base = { secureContext: browser.secureContext, supported: browser.supported };
  if (!base.secureContext || !base.supported) {
    return {
      state: decidePushState({
        ...base,
        serverEnabled: false,
        permission: "default",
        subscribed: false,
      }),
      publicKey: null,
    };
  }
  const config = await server.config();
  const publicKey = config.enabled ? config.public_key : null;
  const registration = await browser.existingRegistration();
  const subscription = (await registration?.pushManager.getSubscription()) ?? null;
  const subscribed =
    subscription !== null && publicKey !== null && keyMatches(subscription, publicKey);
  const state = decidePushState({
    ...base,
    serverEnabled: publicKey !== null,
    permission: browser.permission(),
    subscribed,
  });
  return { state, publicKey };
}

async function subscribeWithKey(manager: PushManagerLike, publicKey: string) {
  const existing = await manager.getSubscription();
  if (existing !== null && keyMatches(existing, publicKey)) return existing;
  // 다른 서버 키로 만든 예전 구독은 해지해야 새 키로 구독할 수 있다.
  if (existing !== null) await existing.unsubscribe();
  try {
    return await manager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: decodeBase64Url(publicKey),
    });
  } catch {
    throw new PushError("푸시 서비스에 등록하지 못했습니다. 잠시 후 다시 시도해 주세요.");
  }
}

// '알림 켜기'. 반드시 버튼 클릭 처리 안에서 바로 불러야 한다: 첫 줄의 권한 요청이 사용자 동작 안에서
// 일어나야 하기 때문이다(아이폰 Safari는 사용자 동작 없이 묻는 것을 막는다).
export async function enablePush(
  browser: PushBrowser,
  server: PushServer,
  publicKey: string,
): Promise<PushState> {
  const permission = await browser.requestPermission();
  if (permission === "denied") return "denied";
  if (permission !== "granted") return "off";
  const registration = await browser.readyRegistration();
  const subscription = await subscribeWithKey(registration.pushManager, publicKey);
  try {
    await server.subscribe(subscriptionBody(subscription.toJSON()));
  } catch (error) {
    // 서버에 저장하지 못한 구독은 쓸모가 없으므로 브라우저 쪽도 되돌린다(화면이 '켜짐'으로 보이지 않게).
    await subscription.unsubscribe().catch(() => false);
    throw error;
  }
  return "on";
}

// '알림 끄기'. 서버에서 지우고 브라우저 구독도 해지한다. 서버 요청이 실패해도 브라우저 구독을 해지하면
// 이 기기로는 더 오지 않고, 서버는 다음 발송 때 410을 받아 구독을 지운다.
export async function disablePush(browser: PushBrowser, server: PushServer): Promise<PushState> {
  const registration = await browser.existingRegistration();
  const subscription = (await registration?.pushManager.getSubscription()) ?? null;
  if (subscription === null) return "off";
  await server.unsubscribe(subscription.endpoint).catch(() => undefined);
  const removed = await subscription.unsubscribe().catch(() => false);
  if (!removed) throw new PushError("이 기기의 알림을 끄지 못했습니다. 다시 시도해 주세요.");
  return "off";
}

// ---- 실제 브라우저와 서버 ----

const SERVICE_WORKER_URL = "/sw.js";
const READY_TIMEOUT_MS = 10_000;

// 서비스 워커(public/sw.js)를 등록한다. 푸시 수신만 하고 캐싱은 하지 않는다.
// updateViaCache: "none"은 sw.js를 고쳤을 때 브라우저 캐시 대신 서버에서 새로 받게 한다.
export function registerServiceWorker(): Promise<ServiceWorkerRegistration> {
  return navigator.serviceWorker.register(SERVICE_WORKER_URL, {
    scope: "/",
    updateViaCache: "none",
  });
}

async function readyWithTimeout(): Promise<ServiceWorkerRegistration> {
  await registerServiceWorker();
  let timer: ReturnType<typeof setTimeout> | undefined;
  const timeout = new Promise<never>((_, reject) => {
    timer = setTimeout(
      () =>
        reject(
          new PushError(
            "알림 기능(서비스 워커)을 준비하지 못했습니다. 새로 고침한 뒤 다시 시도해 주세요.",
          ),
        ),
      READY_TIMEOUT_MS,
    );
  });
  try {
    return await Promise.race([navigator.serviceWorker.ready, timeout]);
  } finally {
    clearTimeout(timer);
  }
}

export function isPushSupported(): boolean {
  return "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
}

export function browserPush(): PushBrowser {
  return {
    secureContext: window.isSecureContext,
    supported: isPushSupported(),
    permission: () => Notification.permission,
    requestPermission: () => Notification.requestPermission(),
    existingRegistration: () => navigator.serviceWorker.getRegistration("/"),
    readyRegistration: readyWithTimeout,
  };
}

const SERVER_ERROR = "서버에 알림 설정을 저장하지 못했습니다. 잠시 후 다시 시도해 주세요.";

async function call<T>(request: Promise<{ response: Response; data?: T }>): Promise<T | undefined> {
  const result = await request.catch(() => {
    throw new PushError("서버에 연결하지 못했습니다. 네트워크를 확인해 주세요.");
  });
  if (!result.response.ok) throw new PushError(SERVER_ERROR);
  return result.data;
}

export const pushServer: PushServer = {
  async config() {
    const data = await call(api.GET("/api/push/public-key"));
    if (data === undefined) throw new PushError(SERVER_ERROR);
    return data;
  },
  async subscribe(body) {
    await call(api.POST("/api/push/subscribe", { body }));
  },
  async unsubscribe(endpoint) {
    await call(api.POST("/api/push/unsubscribe", { body: { endpoint } }));
  },
};
