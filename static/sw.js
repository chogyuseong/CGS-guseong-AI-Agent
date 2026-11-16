// 마이에이전트 서비스 워커. 푸시 알림만 처리한다(오프라인 캐시는 하지 않는다).
// 서버는 /sw.js 주소로 이 파일을 내보낸다. 그래야 사이트 전체를 맡을 수 있다.

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

self.addEventListener("push", (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch (error) {
    data = {};
  }
  const title = data.title || "마이에이전트";
  event.waitUntil(
    self.registration.showNotification(title, {
      body: data.body || "",
      icon: "/static/icons/icon-192.png",
      badge: "/static/icons/icon-192.png",
      data: { url: data.url || "/" },
    })
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  // 알림이 가리키는 주소는 항상 이 사이트 안으로 제한한다.
  let target = new URL((event.notification.data && event.notification.data.url) || "/", self.location.origin);
  if (target.origin !== self.location.origin) {
    target = new URL("/", self.location.origin);
  }
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((windows) => {
      for (const client of windows) {
        if (client.url === target.href && "focus" in client) {
          return client.focus();
        }
      }
      return self.clients.openWindow(target.href);
    })
  );
});
