// 맡은 일: PWA 설정. 폰에서 "홈 화면에 추가"를 하면 이 정보로 앱처럼(주소창 없이) 열린다.
// 경로: Next.js가 이 함수의 결과를 /manifest.webmanifest 주소로 내보내고 <link rel="manifest">도 자동으로 붙인다.
// 쓰는 것: lib/pwa.ts(APP_ICONS 아이콘 경로·크기, THEME_COLORS 색). 아이콘 파일은 public/icons/.
// 근거: docs/DECISIONS.md 9단계 보강(2026-11-18) "PWA" 항목.
//
// 고칠 때 주의:
// - 아이콘을 바꾸면 public/icons/의 PNG 파일과 lib/pwa.ts를 함께 바꾸고, lib/pwa.test.ts(파일이 있고 크기가 맞는지),
//   e2e/dark-mode.spec.ts("theme-color" 묶음: manifest 아이콘 주소가 실제로 image/png로 열리는지)를 돌린다.
//   생성 스크립트는 저장소에 없고 결과 PNG만 있다. 폰에서 "홈 화면에 추가"로 실제 모양도 확인한다.
// - name·start_url·display·theme_color·background_color 값은 lib/pwa.test.ts가 고정한다.
// - start_url을 바꾸면 app/page.tsx의 첫 이동 경로와 맞춘다.
// - name·description은 app/layout.tsx의 metadata와 같은 문구다.

import type { MetadataRoute } from "next";
import { APP_ICONS, THEME_COLORS } from "@/lib/pwa";

// 아이콘(public/icons/)은 단색 바탕에 글자만 있는 그림이다. 경로와 크기는 lib/pwa.test.ts가 확인한다.
// manifest의 theme_color는 값 하나만 받으므로 밝은 색을 두고, 어두운 화면의 색은 layout.tsx의
// <meta name="theme-color" media="(prefers-color-scheme: dark)">가 맡는다.
/**
 * PWA manifest를 만든다. 요청마다 같은 값을 돌려주는 순수 함수다(테스트가 직접 불러 값을 확인한다).
 * APP_ICONS는 `as const`(읽기 전용)라 펼쳐 복사해 Manifest 타입에 맞춘다.
 */
export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "마이에이전트",
    short_name: "마이에이전트",
    description: "채팅으로 시킨 일을 공식 API 도구로 처리하는 개인 AI 비서",
    // 홈 화면 아이콘으로 열 때의 첫 주소. "/"를 거치지 않고 바로 채팅으로 간다.
    start_url: "/chat",
    display: "standalone",
    background_color: THEME_COLORS.light,
    theme_color: THEME_COLORS.light,
    lang: "ko",
    icons: APP_ICONS.map((icon) => ({ ...icon })),
  };
}
