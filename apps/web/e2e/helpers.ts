import { expect, type Page } from "@playwright/test";
import { E2E_EMAIL, E2E_PASSWORD } from "./test-account";

// 실제 API 서버로 로그인하고 채팅 화면(/chat/<번호>)에 도착할 때까지 기다린다.
export async function loginToChat(page: Page): Promise<void> {
  await page.goto("/login");
  await page.getByLabel("이메일").fill(E2E_EMAIL);
  await page.getByLabel("비밀번호").fill(E2E_PASSWORD);
  await page.getByRole("button", { name: "로그인" }).click();
  await expect(page).toHaveURL(/\/chat\/\d+$/);
}
