import { defineConfig } from "@playwright/test";

// Playwright: 실제 브라우저로 화면 흐름을 시험한다. (Django의 LiveServerTestCase + Selenium에 해당)
// 준비 조건: FastAPI 서버가 http://localhost:8000 에 떠 있어야 한다. 테스트 계정은 global-setup에서 만든다.
//
// 브라우저: Playwright가 내려받는 Chromium은 서명이 없어 이 PC의 Smart App Control에 막힌다.
// 그래서 이미 설치된 Google Chrome(서명됨)을 channel: "chrome"으로 쓴다.
//
// 실행: apps/web에서 `pnpm e2e`. global-setup이 http://localhost:8000/api/health를 확인하고 테스트 계정
// (e2e/test-account.ts)을 apps/api의 create-user로 만든다(apps/web/README.md "Playwright").
// 고칠 때 주의:
// - baseURL·webServer의 3000 포트는 package.json의 dev 스크립트(--port 3000)와 맞춘 값이다.
// - API 주소(8000)는 이 파일이 아니라 e2e/global-setup.ts와 next.config.ts(rewrites)에 있다.
// - 프로젝트를 더하면 모든 spec이 그 수만큼 다시 돈다. 폭 점검은 widths.spec.ts의 test.use로 한다(DECISIONS 2026-11-17 화면 점검).
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
  // 준비 확인 주소는 로그인 없이 열리는 /login이다. timeout 120초는 개발 서버가 뜰 때까지 기다리는 최대 시간이다.
  webServer: {
    command: "pnpm dev",
    url: "http://localhost:3000/login",
    reuseExistingServer: true,
    timeout: 120_000,
  },
});
