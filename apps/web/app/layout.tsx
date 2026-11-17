// 루트 레이아웃: 모든 화면을 감싸는 틀이다. (Django의 base.html)
import type { Metadata, Viewport } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import { APPLE_TOUCH_ICON, THEME_COLORS } from "@/lib/pwa";
import { Providers } from "./providers";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "마이에이전트",
  description: "채팅으로 시킨 일을 공식 API 도구로 처리하는 개인 AI 비서",
  appleWebApp: { capable: true, title: "마이에이전트" },
  icons: { apple: APPLE_TOUCH_ICON },
};

// viewportFit: "cover"가 있어야 아이폰 홈 바·노치 영역 크기(safe-area-inset)를 CSS에서 읽을 수 있다.
export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: THEME_COLORS.light },
    { media: "(prefers-color-scheme: dark)", color: THEME_COLORS.dark },
  ],
  // 브라우저 기본 요소(스크롤바, 입력칸, 자동 완성)도 시스템 설정에 맞춰 그린다(globals.css의 color-scheme과 같은 값).
  colorScheme: "light dark",
};

// LayoutProps<"/">는 Next.js가 경로별로 자동 생성하는 props 타입이다. children은 이 틀 안에 들어갈 화면이다.
export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="ko" className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}>
      <body className="min-h-full flex flex-col">
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
