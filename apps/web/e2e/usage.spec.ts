import { expect, type Locator, type Page, test } from "@playwright/test";
import { loginToChat } from "./helpers";
import { fakeUsage } from "./width-fixtures";

// 9단계 2번: 비용 화면이 360px에서 보이고 깨지지 않는지 본다.
// GET /api/usage/daily는 page.route로 흉내 낸다(실제 사용량은 모델 호출로만 쌓여서). 로그인만 실제 API로 간다.
type UsageServer = { requested: number[] };

async function fakeUsageApi(page: Page, empty = false): Promise<UsageServer> {
  const server: UsageServer = { requested: [] };
  await page.route(
    (url) => url.pathname === "/api/usage/daily",
    (route) => {
      const days = Number(new URL(route.request().url()).searchParams.get("days"));
      server.requested.push(days);
      return route.fulfill({ status: 200, json: empty ? emptyUsage(days) : fakeUsage(days) });
    },
  );
  return server;
}

function emptyUsage(days: number) {
  const zero = { input_tokens: 0, output_tokens: 0, total_tokens: 0, cost_usd: 0, calls: 0 };
  const usage = fakeUsage(days);
  return {
    ...usage,
    days: usage.days.map((day) => ({ ...day, ...zero })),
    by_purpose: usage.by_purpose.map((item) => ({ ...item, ...zero })),
    by_model: [],
    total: zero,
    today: { date: "2026-11-17", cost_usd: 0, limit_usd: 1, remaining_usd: 1, exceeded: false },
  };
}

// 로그인 → 하단 탭 '설정' → '비용'으로 들어간다(실제 사용자의 경로).
async function openUsage(page: Page) {
  await loginToChat(page);
  await page
    .getByRole("navigation", { name: "하단 탭" })
    .getByRole("link", { name: "설정" })
    .click();
  await page.getByRole("link", { name: /비용/ }).click();
  await expect(page).toHaveURL(/\/usage$/);
  await expect(page.getByRole("heading", { name: "비용", level: 1 })).toBeVisible();
}

async function expectFits(page: Page, target: Locator) {
  await target.scrollIntoViewIfNeeded();
  const box = await target.boundingBox();
  expect(box).not.toBeNull();
  if (!box) return;
  expect(box.x).toBeGreaterThanOrEqual(0);
  expect(box.x + box.width).toBeLessThanOrEqual(360);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(360);
}

test("360px 비용 화면: 오늘 한도 초과 안내, 일별 표, 용도별·모델별 합계를 보여 주고 기간을 바꾸면 다시 요청한다", async ({
  page,
}) => {
  const server = await fakeUsageApi(page);
  await openUsage(page);

  const today = page.getByRole("region", { name: "오늘" });
  await expect(today).toContainText("$1.0012");
  await expect(today.getByRole("alert")).toContainText(
    "오늘 한도를 넘어 새 작업이 모델을 부르지 않습니다",
  );
  await expect(today.getByRole("progressbar", { name: "오늘 한도 사용률" })).toHaveAttribute(
    "aria-valuenow",
    "100",
  );
  await expectFits(page, today);

  const table = page.getByRole("table", { name: /일별 사용량/ });
  await expect(table.getByRole("row")).toHaveCount(15);
  await expect(table).toContainText("$0.0001 미만");
  await expectFits(page, table);
  const purposes = page.getByRole("region", { name: "용도별 합계" });
  await expect(purposes).toContainText("감시 에이전트(승인 검사)");
  await expectFits(page, purposes);
  await expectFits(page, page.getByRole("region", { name: "모델별 합계" }));

  const month = page.getByRole("button", { name: "30일" });
  const box = await month.boundingBox();
  expect(box?.height ?? 0).toBeGreaterThanOrEqual(44);
  await month.click();
  await expect(table.getByRole("row")).toHaveCount(31);
  await expect(month).toHaveAttribute("aria-pressed", "true");
  expect(server.requested[0]).toBe(14);
  expect(server.requested).toContain(30);
});

test("360px 비용 화면: 사용이 없으면 빈 상태 안내와 오늘 남은 한도만 보여 준다", async ({
  page,
}) => {
  await fakeUsageApi(page, true);
  await openUsage(page);

  await expect(page.getByText("이 기간에는 모델을 부른 기록이 없습니다.")).toBeVisible();
  await expect(page.getByRole("table")).toHaveCount(0);
  const today = page.getByRole("region", { name: "오늘" });
  await expect(today).toContainText("$1.0000");
  await expect(today.getByRole("alert")).toHaveCount(0);
  await expectFits(page, today);
});
