import { expect, type Locator, type Page, test } from "@playwright/test";
import { loginToChat } from "./helpers";

// 설정 화면의 알림 영역이 360px에서 보이고 깨지지 않는지 본다.
// 실제 푸시 구독(브라우저 → 푸시 서비스 등록)과 알림 수신은 자동화로 확인하기 어려워 폰에서 직접 본다
// (docs/PUSH-CHECK.md). 서버 응답(/api/push/public-key)은 상태를 고정하려고 page.route로 정한다.
const PUBLIC_KEY =
  "BEl62iUYgUivxIkv69yViEuiBIa-Ib9-SkvMeAtA3LFgDzkrxZJjSgSnfckjBJuBkr3qBUYIHBQFLXYp5Nksh8U";

async function fakePushConfig(page: Page, enabled: boolean) {
  await page.route("**/api/push/public-key", (route) =>
    route.fulfill({ status: 200, json: { enabled, public_key: enabled ? PUBLIC_KEY : null } }),
  );
}

async function openSettings(page: Page): Promise<Locator> {
  await loginToChat(page);
  await page.getByRole("link", { name: "설정" }).click();
  await expect(page).toHaveURL(/\/settings$/);
  const region = page.getByRole("region", { name: "알림" });
  await expect(region).toBeVisible();
  return region;
}

// 360px 안에 다 들어오고 가로 스크롤이 생기지 않는지.
async function expectFits(page: Page, target: Locator) {
  await target.scrollIntoViewIfNeeded();
  await expect(target).toBeInViewport({ ratio: 1 });
  const box = await target.boundingBox();
  expect(box).not.toBeNull();
  if (!box) return;
  expect(box.x).toBeGreaterThanOrEqual(0);
  expect(box.x + box.width).toBeLessThanOrEqual(360);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(360);
}

test("360px 설정 화면: 알림이 꺼져 있으면 '알림 켜기' 버튼이 화면 안에 보이고 누를 수 있다", async ({
  page,
  context,
}) => {
  // 권한을 '허용'으로 두면 구독이 없는 상태(꺼짐)가 된다. 버튼을 누르지는 않는다(실제 푸시 서비스 등록이 일어남).
  await context.grantPermissions(["notifications"]);
  await fakePushConfig(page, true);
  const region = await openSettings(page);

  await expect(region).toContainText("꺼짐");
  const button = region.getByRole("button", { name: "알림 켜기" });
  await expect(button).toBeEnabled();
  await expectFits(page, region);
  const box = await button.boundingBox();
  expect(box?.height ?? 0).toBeGreaterThanOrEqual(44);
});

test("360px 설정 화면: 서버에 키가 없으면 '서버에 키 없음'을 보여 주고 켜기 버튼을 막는다", async ({
  page,
}) => {
  await fakePushConfig(page, false);
  const region = await openSettings(page);

  await expect(region).toContainText("서버에 키 없음");
  await expect(region.getByRole("button", { name: "알림 켜기" })).toBeDisabled();
  await expectFits(page, region);
});

test("360px 설정 화면: 푸시를 지원하지 않으면 아이폰 홈 화면 추가 안내가 깨지지 않고 보인다", async ({
  page,
}) => {
  // 홈 화면에 추가하지 않은 아이폰 Safari처럼 PushManager가 없는 브라우저를 흉내 낸다.
  await page.addInitScript(() => {
    Reflect.deleteProperty(window, "PushManager");
  });
  const region = await openSettings(page);

  await expect(region).toContainText("지원 안 함");
  await expect(region).toContainText("홈 화면에 추가");
  await expect(region.getByRole("button")).toHaveCount(0);
  await expectFits(page, region);
});
