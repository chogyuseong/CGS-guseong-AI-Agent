// 루트 레이아웃: 모든 화면을 감싸는 틀이다. (Django의 base.html)
//
// 맡은 일: <html>·<body> 뼈대, 글꼴 변수, 페이지 제목·설명(metadata), 모바일 화면 설정(viewport),
//   theme-color(주소창·상태 표시줄 색), 공통 Provider(TanStack Query 캐시 + 서비스 워커 등록) 연결.
// 경로: 모든 URL. 로그인 확인과 하단 탭은 여기가 아니라 app/(tabs)/layout.tsx가 맡는다(/login은 탭 없이 이 틀만 쓴다).
// 쓰는 것: ./providers(Providers), lib/pwa.ts(THEME_COLORS, APPLE_TOUCH_ICON), ./globals.css(색 토큰).
// 근거: docs/DECISIONS.md 9단계 보강(2026-11-18) — 다크 모드는 시스템 설정(prefers-color-scheme)만 따르고
//   앱 안의 테마 선택은 두지 않는다. 그래서 <html>에 dark 클래스를 붙이는 코드가 없다.
//   PWA theme-color는 밝음·어둠 두 개의 <meta>로 낸다(manifest는 값 하나만 받아서).
//
// 고칠 때 주의:
// - themeColor·colorScheme·appleWebApp·icons를 바꾸면 e2e/dark-mode.spec.ts("theme-color" 묶음: meta 2개와
//   media 값, apple-touch-icon 응답)와 lib/pwa.test.ts를 함께 확인한다. 색 값 자체는 lib/pwa.ts에서 바꾼다.
// - title·description은 app/manifest.ts의 name·description과 같은 문구다. 한쪽만 바꾸지 않는다.
// - 글꼴: Geist는 "--font-geist-sans"·"--font-geist-mono" 변수로 붙는다. globals.css의 @theme는
//   --font-mono만 --font-geist-mono를 가리키고 --font-sans는 자기 자신(var(--font-sans))을 가리킨다.
//   본문 글꼴을 바꿀 때는 이 연결을 globals.css에서 먼저 확인한다.
import type { Metadata, Viewport } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import { APPLE_TOUCH_ICON, THEME_COLORS } from "@/lib/pwa";
import { Providers } from "./providers";
import "./globals.css";

// next/font는 빌드 때 글꼴을 내려받아 같은 출처에서 제공한다(실행 중 Google Fonts를 부르지 않는다).
// subsets는 latin만 받는다(한글 글자는 기기 글꼴로 그려진다).
const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

// 탭 제목·검색 설명과 iOS "홈 화면에 추가" 설정. apple-touch-icon(180x180)은 lib/pwa.ts 한 곳에서 경로를 정한다.
export const metadata: Metadata = {
  title: "마이에이전트",
  description: "채팅으로 시킨 일을 공식 API 도구로 처리하는 개인 AI 비서",
  appleWebApp: { capable: true, title: "마이에이전트" },
  icons: { apple: APPLE_TOUCH_ICON },
};

// viewportFit: "cover"가 있어야 아이폰 홈 바·노치 영역 크기(safe-area-inset)를 CSS에서 읽을 수 있다.
// (tabs)/layout.tsx·login/page.tsx·BottomTabs 등이 env(safe-area-inset-*)를 쓰므로 이 값을 빼면 노치 아래로 글자가 가려진다.
export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
  // 밝음·어둠 각각의 상태 표시줄 색. globals.css의 --background와 같은 색이어야 화면과 이어져 보인다(lib/pwa.ts 주석).
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: THEME_COLORS.light },
    { media: "(prefers-color-scheme: dark)", color: THEME_COLORS.dark },
  ],
  // 브라우저 기본 요소(스크롤바, 입력칸, 자동 완성)도 시스템 설정에 맞춰 그린다(globals.css의 color-scheme과 같은 값).
  colorScheme: "light dark",
};

/**
 * 모든 화면의 바깥 틀.
 *
 * LayoutProps<"/">는 Next.js가 경로별로 자동 생성하는 props 타입이다(`pnpm typecheck`의 next typegen이 만든다).
 * children은 이 틀 안에 들어갈 화면이다. lang="ko"는 화면 읽기 프로그램의 발음과 줄바꿈 규칙에 쓰인다.
 */
export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="ko" className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}>
      <body className="min-h-full flex flex-col">
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
