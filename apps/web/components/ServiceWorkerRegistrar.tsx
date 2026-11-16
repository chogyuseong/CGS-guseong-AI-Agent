"use client";

import { useEffect } from "react";
import { isPushSupported, registerServiceWorker } from "@/lib/push";

// 앱을 열 때 서비스 워커(public/sw.js)를 등록한다. sw.js는 푸시 수신과 알림 클릭만 처리하고
// 캐싱·오프라인 기능은 없다. 등록해 두면 sw.js를 고쳤을 때 다음 방문에서 새 버전으로 바뀐다.
// https(또는 localhost)가 아니거나 지원하지 않는 브라우저에서는 아무것도 하지 않는다.
export function ServiceWorkerRegistrar() {
  useEffect(() => {
    if (!window.isSecureContext || !isPushSupported()) return;
    registerServiceWorker().catch(() => {
      // 등록 실패는 화면 사용에 영향이 없다. '알림 켜기'를 누를 때 다시 등록하고 그때 오류를 보여 준다.
    });
  }, []);
  return null;
}
