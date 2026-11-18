"use client";

import { useEffect } from "react";
import { isPushSupported, registerServiceWorker } from "@/lib/push";

// 앱을 열 때 서비스 워커(public/sw.js)를 등록한다. sw.js는 푸시 수신과 알림 클릭만 처리하고
// 캐싱·오프라인 기능은 없다. 등록해 두면 sw.js를 고쳤을 때 다음 방문에서 새 버전으로 바뀐다.
// https(또는 localhost)가 아니거나 지원하지 않는 브라우저에서는 아무것도 하지 않는다.
//
// 쓰는 곳: app/providers.tsx(모든 화면에 한 번). 등록 함수는 lib/push.ts의 registerServiceWorker
//   (scope "/", updateViaCache "none"). '알림 켜기'(enablePush)도 같은 함수로 다시 등록한 뒤 활성화를 최대 10초 기다린다.
// 근거: DECISIONS 2026-11-17 "서비스 워커는 앱을 열 때 등록하고 '알림 켜기' 때도 다시 등록한다".
//
// 고칠 때 주의:
// - 여기서는 등록만 한다. 알림 권한 요청이나 구독 전송을 넣지 않는다(권한 요청은 클릭 안에서만 해야 하고,
//   구독 자동 전송은 이 기기 알림을 다른 계정으로 옮길 수 있다. PushSettings.tsx 참고).
// - sw.js는 install에서 skipWaiting, activate에서 clients.claim만 하고 캐싱·fetch 처리는 없다(DECISIONS 같은 날).
// - 이 컴포넌트 전용 단위 테스트는 없다. sw.js의 push·notificationclick 처리는 lib/sw.test.ts가 본다.
export function ServiceWorkerRegistrar() {
  useEffect(() => {
    if (!window.isSecureContext || !isPushSupported()) return;
    registerServiceWorker().catch(() => {
      // 등록 실패는 화면 사용에 영향이 없다. '알림 켜기'를 누를 때 다시 등록하고 그때 오류를 보여 준다.
    });
  }, []);
  return null;
}
