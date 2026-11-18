import { expect, test } from "@playwright/test";
import { E2E_EMAIL, E2E_PASSWORD } from "./test-account";

const TAB_LABELS = ["채팅", "할 일", "작업 기록", "자동화", "기억", "설정"];

// 0단계 완료 기준: 360px 폭에서 로그인 → 채팅 화면 → 하단 탭 6개가 보인다.
test("360px 폭에서 로그인하면 채팅 화면과 하단 탭 6개가 보인다", async ({ page }) => {
  // 로그인 안 한 상태로 채팅을 열면 로그인 화면으로 보내져야 한다.
  await page.goto("/chat");
  await expect(page).toHaveURL(/\/login$/);

  await page.getByLabel("이메일").fill(E2E_EMAIL);
  await page.getByLabel("비밀번호").fill(E2E_PASSWORD);
  await page.getByRole("button", { name: "로그인" }).click();

  // /chat은 최근 대화(없으면 새 대화)인 /chat/<번호>로 이어진다.
  await expect(page).toHaveURL(/\/chat\/\d+$/);
  await expect(page.getByLabel("메시지 입력")).toBeVisible();

  const nav = page.getByRole("navigation", { name: "하단 탭" });
  const links = nav.getByRole("link");
  await expect(links).toHaveCount(6);
  for (const label of TAB_LABELS) {
    // toBeInViewport: 요소가 실제로 화면(360x740) 안에 보이는지까지 확인한다.
    await expect(nav.getByRole("link", { name: label })).toBeInViewport();
  }
});
