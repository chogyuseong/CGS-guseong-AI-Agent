import type { MetadataRoute } from "next";
import { APP_ICONS, THEME_COLORS } from "@/lib/pwa";

// PWA 설정. 폰에서 "홈 화면에 추가"를 하면 이 정보로 앱처럼 열린다.
// Next.js가 이 함수의 결과를 /manifest.webmanifest 주소로 내보낸다.
// 아이콘(public/icons/)은 단색 바탕에 글자만 있는 그림이다. 경로와 크기는 lib/pwa.test.ts가 확인한다.
// manifest의 theme_color는 값 하나만 받으므로 밝은 색을 두고, 어두운 화면의 색은 layout.tsx의
// <meta name="theme-color" media="(prefers-color-scheme: dark)">가 맡는다.
export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "마이에이전트",
    short_name: "마이에이전트",
    description: "채팅으로 시킨 일을 공식 API 도구로 처리하는 개인 AI 비서",
    start_url: "/chat",
    display: "standalone",
    background_color: THEME_COLORS.light,
    theme_color: THEME_COLORS.light,
    lang: "ko",
    icons: APP_ICONS.map((icon) => ({ ...icon })),
  };
}
