// 마이에이전트 서비스 워커: 웹 푸시를 받아 알림을 띄우고, 알림을 누르면 앱의 해당 화면을 연다.
// 캐싱·오프라인 기능은 없다(명세에 없음). 그래서 fetch 이벤트를 다루지 않는다.
//
// 서버 payload 모양: {"title": "마이에이전트", "body": 고정 문구, "url": "/chat/{대화 id}", "kind": ...}
// payload는 그대로 믿지 않는다: 글자가 아니면 기본 문구를 쓰고, url은 이 앱(같은 출처)의 경로일 때만 쓴다.
// kind는 읽지 않는다(알림 문구는 서버가 kind별로 고른 body를 그대로 쓴다).
//
// 등록: lib/push.ts의 registerServiceWorker(scope "/", updateViaCache "none")를 components/ServiceWorkerRegistrar.tsx가
// 앱을 열 때, PushSettings의 '알림 켜기'가 켤 때 부른다. 응답 헤더(no-cache, CSP)는 next.config.ts의 headers().
// 보내는 쪽: apps/api/app/push.py(build_payload, PUSH_BODIES). 근거: DECISIONS 2026-11-17 화면 절(sw.js는 payload를 믿지 않음,
// install에서 skipWaiting·activate에서 clients.claim만), 푸시 본문은 고정 문구라 요청 내용이 들어갈 자리가 없음.
//
// 고칠 때 주의:
// - 이 파일은 번들되지 않는 일반 스크립트다(import·TypeScript 불가, 브라우저가 그대로 실행). 고친 뒤 기존 기기는 다음
//   등록 확인 때 새 파일을 받는다(updateViaCache "none" + no-cache 헤더).
// - payload 칸 이름(title·body·url)을 바꾸면 서버 push.py의 build_payload와 같이 바꾼다.
// - fetch 처리를 더하면 캐싱 정책이 생긴다. 명세에 없는 기능이라 넣지 않는다.
// - 테스트: lib/sw.test.ts(node:vm으로 이 파일을 실제 실행). 폰 확인은 docs/PUSH-CHECK.md.

const DEFAULT_TITLE = "마이에이전트";
const DEFAULT_BODY = "새 알림이 있습니다.";
// url이 없거나 앱 밖 주소면 가는 곳(채팅 탭).
const DEFAULT_PATH = "/chat";
// public/icons/icon-192.png. 아이콘 파일을 옮기면 lib/pwa.ts의 경로와 함께 여기도 바꾼다.
const ICON = "/icons/icon-192.png";

// 같은 출처의 경로("/..."로 시작)만 통과시키고 나머지는 /chat으로 바꾼다.
// "//다른주소", "/\다른주소"처럼 다른 출처로 풀리는 값도 URL로 풀어 본 뒤 출처를 비교해 막는다.
function safePath(value) {
  if (typeof value !== "string" || !value.startsWith("/")) return DEFAULT_PATH;
  try {
    const url = new URL(value, self.location.origin);
    if (url.origin !== self.location.origin) return DEFAULT_PATH;
    return url.pathname + url.search + url.hash;
  } catch {
    return DEFAULT_PATH;
  }
}

// 글자가 아니거나 비었으면 fallback, 길면 maxLength에서 자른다(제목 100자·본문 300자. 서버 고정 문구는 이보다 짧다).
function text(value, fallback, maxLength) {
  if (typeof value !== "string" || value.trim() === "") return fallback;
  return value.slice(0, maxLength);
}

// 푸시 본문을 객체로. 비었거나 JSON이 아니면 {}라서 기본 문구 알림이 뜬다(푸시를 받으면 알림은 반드시 띄운다:
// lib/push.ts가 userVisibleOnly: true로 구독했기 때문).
function readPayload(event) {
  try {
    const data = event.data ? event.data.json() : null;
    return data !== null && typeof data === "object" ? data : {};
  } catch {
    return {};
  }
}

function isSameOrigin(url) {
  try {
    return new URL(url).origin === self.location.origin;
  } catch {
    return false;
  }
}

// 이미 열린 앱 창이 있으면 그 창을 앞으로 가져와 이동하고, 없으면 새 창을 연다.
// 같은 출처의 창만 고른다: 다른 사이트 창을 움직이지 않게. target은 safePath를 거친 같은 출처 주소여야 한다.
async function openTarget(target) {
  const windows = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
  const ours = windows.filter((client) => isSameOrigin(client.url));
  const exact = ours.find((client) => client.url === target);
  if (exact) return exact.focus();
  const first = ours[0];
  if (first) {
    try {
      await first.focus();
      await first.navigate(target);
      return;
    } catch {
      // 이 서비스 워커가 제어하지 않는 창은 navigate가 실패한다. 그때는 새 창으로 연다.
    }
  }
  return self.clients.openWindow(target);
}

// 새 버전의 sw.js를 바로 쓰고, 이미 열린 창도 이 서비스 워커가 맡게 한다(알림 클릭 때 창 이동이 되도록).
self.addEventListener("install", () => {
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener("push", (event) => {
  const payload = readPayload(event);
  const title = text(payload.title, DEFAULT_TITLE, 100);
  const options = {
    body: text(payload.body, DEFAULT_BODY, 300),
    icon: ICON,
    badge: ICON,
    lang: "ko",
    data: { url: safePath(payload.url) },
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const data = event.notification.data;
  // 알림에 저장된 url도 한 번 더 검사한다.
  const target = new URL(safePath(data ? data.url : undefined), self.location.origin).href;
  event.waitUntil(openTarget(target));
});
