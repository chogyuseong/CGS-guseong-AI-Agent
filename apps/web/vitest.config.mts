import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// Vitest: 컴포넌트 단위 테스트. (pytest로 함수 하나를 시험하는 것과 같은 자리)
// Playwright(e2e/) 파일은 Vitest가 아니라 Playwright가 돌리므로 제외한다.
export default defineConfig({
  plugins: [react()],
  resolve: {
    // tsconfig.json의 "@/*" 경로 별칭을 그대로 쓴다(Vite 8 내장 기능).
    tsconfigPaths: true,
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./vitest.setup.ts"],
    include: ["**/*.test.{ts,tsx}"],
    exclude: ["node_modules/**", ".next/**", "e2e/**"],
  },
});
