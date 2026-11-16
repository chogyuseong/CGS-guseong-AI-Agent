import { defineConfig } from "@playwright/test";

// Playwright: 실제 브라우저로 화면 흐름을 시험한다. (Django의 LiveServerTestCase + Selenium에 해당)
// 준비 조건: FastAPI 서버가 http://localhost:8000 에 떠 있어야 한다. 테스트 계정은 global-setup에서 만든다.
//
// 브라우저: Playwright가 내려받는 Chromium은 서명이 없어 이 PC의 Smart App Control에 막힌다.
// 그래서 이미 설치된 Google Chrome(서명됨)을 channel: "chrome"으로 쓴다.
export default defineConfig({
  testDir: "./e2e",
  globalSetup: "./e2e/global-setup.ts",
  fullyParallel: false,
  // 모든 테스트가 같은 e2e 계정을 쓰므로 한 번에 하나씩 돌린다(첫 대화가 동시에 두 번 만들어지지 않게).
  workers: 1,
  retries: 0,
  reporter: "list",
  use: {
    baseURL: "http://localhost:3000",
    channel: "chrome",
    trace: "retain-on-failure",
  },
  // 폭별 프로젝트는 두지 않는다(전체 e2e 시간이 폭 수만큼 늘어남). 360·390·768·1280px 점검은
  // e2e/widths.spec.ts 한 파일이 test.use로 폭을 바꿔 가며 돈다(9단계 1번).
  projects: [
    {
      // 명세의 기준 폭. 폰 화면(360x740)과 터치 입력을 흉내 낸다.
      name: "mobile-360",
      use: {
        viewport: { width: 360, height: 740 },
        isMobile: true,
        hasTouch: true,
        deviceScaleFactor: 3,
      },
    },
  ],
  // 테스트 전에 Next 개발 서버를 띄우고, 끝나면 끈다. 이미 떠 있으면 그것을 쓴다.
  webServer: {
    command: "pnpm dev",
    url: "http://localhost:3000/login",
    reuseExistingServer: true,
    timeout: 120_000,
  },
});
