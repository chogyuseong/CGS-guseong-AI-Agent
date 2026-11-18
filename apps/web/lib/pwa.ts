// PWA 아이콘 경로와 theme-color(9단계 보강). manifest(app/manifest.ts)와 루트 레이아웃(app/layout.tsx)이 함께 쓴다.
// 파일이 실제로 있고 크기가 맞는지는 lib/pwa.test.ts가 확인한다.
// 근거: DECISIONS 2026-11-18 9단계 보강 "PWA"(manifest theme_color는 값 하나라 밝은 색, 어두운 화면은
// layout.tsx의 media="(prefers-color-scheme: dark)" theme-color 메타가 맡는다), "다크 모드는 시스템 설정만 따른다".
//
// 고칠 때 주의:
// - 아이콘 파일은 public/icons/에 있다. 경로를 바꾸면 public/sw.js의 ICON(알림 아이콘, 글자로 적혀 있음)도 같이 바꾼다.
// - THEME_COLORS는 app/globals.css의 --background와 손으로 맞춘 값이다. 배경 토큰을 바꾸면 여기도 바꾼다.

// 브라우저 주소창·상태 표시줄 색. globals.css의 --background(밝음 oklch(1 0 0), 어둠 oklch(0.145 0 0))와 같은 값이다.
export const THEME_COLORS = { light: "#ffffff", dark: "#0a0a0a" } as const;

export const APP_ICONS = [
  { src: "/icons/icon-192.png", sizes: "192x192", type: "image/png" },
  { src: "/icons/icon-512.png", sizes: "512x512", type: "image/png" },
  // maskable: 안드로이드가 원·둥근 사각형으로 잘라도 글자가 남도록 안전 영역 안에 그린 아이콘.
  {
    src: "/icons/icon-maskable-512.png",
    sizes: "512x512",
    type: "image/png",
    purpose: "maskable",
  },
] as const;

// iOS "홈 화면에 추가" 아이콘(180x180).
export const APPLE_TOUCH_ICON = "/icons/apple-touch-icon.png";
