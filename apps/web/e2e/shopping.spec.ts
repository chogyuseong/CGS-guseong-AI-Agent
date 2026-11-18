// 2026-11-18 생활 비서 A단계. 모델·쇼핑몰 호출 없이 채팅에 표시되는 실제 링크와 판정 근거를 검사한다.
// 고칠 때 주의: API의 compare_products 결과 그룹 이름을 바꾸면 이 fixture도 같이 바꾼다.
import { expect, test } from "@playwright/test";
import { loginToChat } from "./helpers";
import { fakeChat } from "./width-fixtures";

test("채팅 답변 아래에서 배송비 포함 가격과 판매처 링크를 확인한다", async ({ page }) => {
  await fakeChat(page);
  await page.route("**/api/tasks/*", async (route) => {
    const row = {
      상품: "KB100 한글 갈축",
      "비교 가격": "92,500원",
      링크: "https://example.org/products/3",
    };
    const output = {
      status: "succeeded",
      data: { notice: "판매처 정보", content: { "조건 일치": [row] }, truncated: false },
      sources: [],
      fetched_at: "2026-11-18T00:00:00Z",
    };
    const step = {
      id: 1,
      order: 1,
      tool_name: "compare_products",
      label: "상품 비교",
      status: "done",
      output,
    };
    await route.fulfill({ json: { id: 1, status: "done", steps: [step], approvals: [] } });
  });
  await loginToChat(page);
  const card = page.getByRole("region", { name: "외부에서 가져온 내용" }).first();
  await expect(card).toContainText("92,500원");
  await expect(card).toContainText("조회 시각");
  await expect(card.getByRole("link")).toHaveAttribute("href", "https://example.org/products/3");
});
