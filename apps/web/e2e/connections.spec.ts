import { expect, type Locator, type Page, test } from "@playwright/test";
import { loginToChat } from "./helpers";

// 4단계: 외부 서비스 연결 화면(/connections)이 360px에서 보이고 깨지지 않는지 본다.
// 연결 상태(/api/connections)와 해제(DELETE)는 page.route로 흉내 낸다. 실제 Google 동의 화면으로는 이동하지 않는다:
// 4단계 보강: 연결 시작은 POST다. 가짜 서버는 Google 주소 대신 앱 안의 콜백 결과 주소를 돌려줘, 버튼이 받은 주소로
// 이동하는지만 본다(실제 Google로 나가는 요청은 없다).
type FakeConnection = {
  provider: "google";
  available: boolean;
  connected: boolean;
  calendar: boolean;
  mail: boolean;
  scopes: string[];
  connected_at: string | null;
};

const NOT_CONNECTED: FakeConnection = {
  provider: "google",
  available: true,
  connected: false,
  calendar: false,
  mail: false,
  scopes: [],
  connected_at: null,
};
const CONNECTED: FakeConnection = {
  ...NOT_CONNECTED,
  connected: true,
  calendar: true,
  scopes: ["https://www.googleapis.com/auth/calendar.events.owned"],
  connected_at: "2026-11-16T16:30:00Z",
};

async function fakeConnections(page: Page, initial: FakeConnection) {
  const server = { connection: initial, deletes: 0, starts: [] as unknown[] };
  await page.route("**/api/connections", (route) =>
    route.fulfill({ status: 200, json: [server.connection] }),
  );
  await page.route("**/api/connections/google", (route) => {
    if (route.request().method() !== "DELETE") return route.abort();
    server.deletes += 1;
    server.connection = NOT_CONNECTED;
    return route.fulfill({ status: 204 });
  });
  await page.route("**/api/connections/google/start", (route) => {
    if (route.request().method() !== "POST") return route.abort();
    server.starts.push(route.request().postDataJSON());
    // 실제 서버는 Google 동의 화면 주소를 준다. 여기서는 앱 안의 성공 주소로 돌려 이동만 확인한다.
    return route.fulfill({
      status: 200,
      json: { authorization_url: "/connections?google=connected" },
    });
  });
  return server;
}

// 로그인 → 설정 탭 → 'Google 계정 연결' 링크로 들어간다(실제 사용자의 경로).
async function openConnections(page: Page): Promise<Locator> {
  await loginToChat(page);
  await page.getByRole("link", { name: "설정", exact: true }).click();
  await page.getByRole("link", { name: /Google 계정 연결/ }).click();
  await expect(page).toHaveURL(/\/connections$/);
  const region = page.getByRole("region", { name: "Google 계정" });
  await expect(region).toBeVisible();
  return region;
}

// 360px 안에 다 들어오고, 손가락으로 누를 만한 높이(44px 이상)인지.
async function expectTappable(target: Locator) {
  await target.scrollIntoViewIfNeeded();
  await expect(target).toBeInViewport({ ratio: 1 });
  const box = await target.boundingBox();
  expect(box).not.toBeNull();
  if (!box) return;
  expect(box.height).toBeGreaterThanOrEqual(44);
  expect(box.x).toBeGreaterThanOrEqual(0);
  expect(box.x + box.width).toBeLessThanOrEqual(360);
}

async function expectNoHorizontalScroll(page: Page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(360);
}

test("360px 연결 화면: 연결 안 됨이면 권한 안내와 연결 버튼을 보여 주고, 누르면 POST 후 받은 주소로 이동한다", async ({
  page,
}) => {
  const server = await fakeConnections(page, NOT_CONNECTED);
  const region = await openConnections(page);

  await expect(region).toContainText("연결 안 됨");
  await expect(region).toContainText("메일은 요청하지 않습니다");
  await expect(region).toContainText("매번 승인을 묻습니다");
  const button = region.getByRole("button", { name: "Google 계정 연결" });
  await expectTappable(button);
  await expectNoHorizontalScroll(page);
  // 연결 화면은 설정 탭 안의 화면이다.
  const tabs = page.getByRole("navigation", { name: "하단 탭" });
  await expect(tabs.getByRole("link", { name: "설정" })).toHaveAttribute("aria-current", "page");

  await button.click();
  await expect(
    page.getByRole("status").filter({ hasText: "Google 계정을 연결했습니다" }),
  ).toBeVisible();
  expect(server.starts).toEqual([{}]);
});

test("360px 연결 화면: 연결됨이면 날짜와 권한을 보여 주고, 화면 안에서 한 번 더 물은 뒤 해제한다", async ({
  page,
}) => {
  const server = await fakeConnections(page, CONNECTED);
  const region = await openConnections(page);

  await expect(region).toContainText("연결됨");
  await expect(region).toContainText("2026년 11월 17일");
  await expectTappable(region.getByRole("button", { name: "다시 연결" }));
  const disconnect = region.getByRole("button", { name: "연결 해제" });
  await expectTappable(disconnect);

  await disconnect.click();
  await expect(region).toContainText("Google 연결을 해제할까요?");
  expect(server.deletes).toBe(0);
  const confirm = region.getByRole("button", { name: "해제하기" });
  await expectTappable(confirm);
  await expectTappable(region.getByRole("button", { name: "취소" }));
  await expectNoHorizontalScroll(page);

  await confirm.click();
  await expect(region).toContainText("연결 안 됨");
  await expect(region).toContainText("Google 연결을 해제했습니다.");
  expect(server.deletes).toBe(1);
});

test("360px 연결 화면: 돌아온 주소의 결과 코드별로 한국어 안내를 보여 주고 쿼리를 지운다", async ({
  page,
}) => {
  await fakeConnections(page, NOT_CONNECTED);
  await loginToChat(page);
  const main = page.getByRole("main");
  const cases = [
    ["google=error&reason=scope_missing", "alert", "캘린더 권한을 허용하지 않아"],
    ["google=error&reason=access_denied", "alert", "허용하지 않아 연결하지 않았습니다"],
    ["google=error&reason=not_configured", "alert", "서버에 Google 연결 설정이 없어"],
    ["google=error&reason=%3Cb%3Ex%3C%2Fb%3E", "alert", "Google 계정을 연결하지 못했습니다"],
    ["google=connected", "status", "Google 계정을 연결했습니다"],
    ["google=connected&added=mail", "status", "메일 읽기 권한을 추가했습니다"],
    ["google=error&reason=mail_scope_missing", "alert", "캘린더 연결은 그대로 저장했습니다"],
  ] as const;

  for (const [query, role, text] of cases) {
    await page.goto(`/connections?${query}`);
    await expect(main.getByRole(role).filter({ hasText: text })).toBeVisible();
    await expect(page).toHaveURL(/\/connections$/);
    await expectNoHorizontalScroll(page);
  }
  // 주소의 글자(<b>x</b>)를 화면에 옮기지 않는다.
  await page.goto("/connections?google=error&reason=%3Cb%3Ex%3C%2Fb%3E");
  await expect(main.getByRole("alert")).toBeVisible();
  await expect(main).not.toContainText("<b>x</b>");
});

test("360px 연결 화면: 서버 설정이 없으면 '서버 설정 없음'을 보여 주고 연결 버튼을 막는다", async ({
  page,
}) => {
  await fakeConnections(page, { ...NOT_CONNECTED, available: false });
  const region = await openConnections(page);

  await expect(region).toContainText("서버 설정 없음");
  await expect(region).toContainText(".env");
  await expect(region.getByRole("link")).toHaveCount(0);
  const button = region.getByRole("button", { name: "Google 계정 연결" });
  await expect(button).toBeDisabled();
  const box = await button.boundingBox();
  expect(box?.height ?? 0).toBeGreaterThanOrEqual(44);
  await expectNoHorizontalScroll(page);
});

test("360px 연결 화면(7단계): 메일 읽기 권한이 없으면 상태·안내와 추가 버튼을 보여 주고 scope=mail로 시작한다", async ({
  page,
}) => {
  const server = await fakeConnections(page, CONNECTED);
  const region = await openConnections(page);
  const mail = region.getByRole("region", { name: "메일 읽기 권한" });

  await expect(mail).toContainText("없음");
  await expect(mail).toContainText("보내기·답장·삭제·라벨 변경은 하지 않습니다");
  await expect(mail).toContainText("본문은 4,000자까지만 읽고 첨부는 이름만 봅니다");
  await expect(mail).toContainText("예약 작업과 조건 감시는 메일을 읽지 않습니다");
  await expect(mail).toContainText("확인되지 않은 앱");
  const add = mail.getByRole("button", { name: "메일 읽기 권한 추가" });
  await expectTappable(add);
  await expectNoHorizontalScroll(page);
  await add.click();
  await expect(page).toHaveURL(/\/connections/);
  expect(server.starts).toEqual([{ scope: "mail" }]);
});

test("360px 연결 화면(7단계): 메일 권한이 있으면 '있음'과 빼는 방법을 안내하고 추가 버튼은 없다", async ({
  page,
}) => {
  await fakeConnections(page, { ...CONNECTED, mail: true });
  const region = await openConnections(page);
  const mail = region.getByRole("region", { name: "메일 읽기 권한" });

  await expect(mail).toContainText("있음");
  await expect(mail).toContainText("메일 권한만 끄는 방법은 없습니다");
  await expect(mail).toContainText("연결 해제 후 캘린더만 다시 연결");
  await expect(mail.getByRole("button", { name: "메일 읽기 권한 추가" })).toHaveCount(0);
  await expectNoHorizontalScroll(page);
});
