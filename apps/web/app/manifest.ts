import type { MetadataRoute } from "next";

// PWA 설정. 폰에서 "홈 화면에 추가"를 하면 이 정보로 앱처럼 열린다.
// Next.js가 이 함수의 결과를 /manifest.webmanifest 주소로 내보낸다.
// 아이콘(public/icons/)은 0단계에서는 자리만 잡은 단색 그림이다. 디자인은 나중 단계에서 바꾼다.
export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "마이에이전트",
    short_name: "마이에이전트",
    description: "채팅으로 시킨 일을 공식 API 도구로 처리하는 개인 AI 비서",
    start_url: "/chat",
    display: "standalone",
    background_color: "#ffffff",
    theme_color: "#ffffff",
    lang: "ko",
    icons: [
      { src: "/icons/icon-192.png", sizes: "192x192", type: "image/png" },
      { src: "/icons/icon-512.png", sizes: "512x512", type: "image/png" },
    ],
  };
}
