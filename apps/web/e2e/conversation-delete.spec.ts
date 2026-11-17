import { expect, test } from "@playwright/test";
import { loginToChat } from "./helpers";

// 5단계 보강: 대화 목록 시트에서 지금 대화를 지운다(실제 API 서버). 360px에서 확인 영역이 화면 안에 보이고,
// 지우면 그 대화가 목록에서 사라지고 다른 대화(없으면 새 대화)로 이동한다.
test("360px 대화 지우기: 한 번 더 묻고, 지우면 목록에서 사라진다", async ({ page }) => {
  await loginToChat(page);
  const firstUrl = page.url();
  await page.getByRole("button", { name: "대화 목록" }).click();
  await page.getByRole("button", { name: "새 대화" }).click();
  // 새 대화 주소로 실제로 바뀔 때까지 기다린다(바뀌기 전 주소를 읽지 않게).
  await expect(page).not.toHaveURL(firstUrl);
  await expect(page).toHaveURL(/\/chat\/\d+$/);
  const deletedId = Number(new URL(page.url()).pathname.split("/").pop());

  await page.getByRole("button", { name: "대화 목록" }).click();
  await page.getByRole("button", { name: "이 대화 지우기" }).click();
  const confirm = page.getByRole("group", { name: "대화 지우기 확인" });
  await expect(confirm).toContainText("이 대화에서 만든 기억은 남습니다");
  const submit = confirm.getByRole("button", { name: "지우기" });
  await submit.scrollIntoViewIfNeeded();
  await expect(submit).toBeInViewport({ ratio: 1 });
  const box = await submit.boundingBox();
  expect(box?.height ?? 0).toBeGreaterThanOrEqual(44);

  await submit.click();

  // 지운 대화 주소를 떠나 다른 대화로 갈 때까지 기다린다.
  await expect(page).not.toHaveURL(new RegExp(`/chat/${deletedId}$`));
  await expect(page).toHaveURL(/\/chat\/\d+$/);
  await page.getByRole("button", { name: "대화 목록" }).click();
  await expect(page.locator(`a[href="/chat/${deletedId}"]`)).toHaveCount(0);
});
