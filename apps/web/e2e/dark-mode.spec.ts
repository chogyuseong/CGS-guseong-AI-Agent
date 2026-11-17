import { expect, type Page, test } from "@playwright/test";
import { auditContrast } from "./contrast-audit";
import { loginToChat } from "./helpers";
import { CHAT_ID, fakeChat, fakeTabs } from "./width-fixtures";

// 9단계 보강: 다크 모드와 명도 대비(BUILD_SPEC 10.1). 360px에서 시스템 설정이 어둠·밝음일 때
// 모든 화면의 글자 대비를 잰다(contrast-audit.ts). 데이터는 폭 점검과 같은 흉내 데이터(width-fixtures.ts)다.
// 스크린샷: CONTRAST_SHOTS_DIR 환경 변수에 저장소 밖 폴더를 주면 화면마다 남긴다.
const SCHEMES = ["dark", "light"] as const;

const TAB_SCREENS = [
  ["/automation", "자동화"],
  ["/memory", "기억"],
  ["/settings", "설정"],
  ["/connections", "외부 서비스 연결"],
  ["/usage", "비용"],
] as const;

for (const scheme of SCHEMES) {
  test.describe(`${scheme === "dark" ? "어두운" : "밝은"} 화면`, () => {
    test.use({ colorScheme: scheme });

    test("로그인 화면", async ({ page }) => {
      await page.goto("/login");
      await expect(page.getByRole("button", { name: "로그인" })).toBeVisible();
      await expectBackground(page, scheme);
      await auditContrast(page, `${scheme}-login`);
    });

    test("채팅: 단계 타임라인·외부 콘텐츠·초안 카드·승인 카드·음성 안내·대화 목록", async ({
      page,
    }) => {
      await fakeChat(page);
      await loginToChat(page);
      await expect(page).toHaveURL(new RegExp(`/chat/${CHAT_ID}$`));
      await checkChat(page, scheme);
    });

    test("탭 화면: 작업 기록·자동화·기억·설정·연결·비용", async ({ page }) => {
      await fakeChat(page);
      await fakeTabs(page);
      await loginToChat(page);
      await page.goto("/tasks");
      await page
        .getByRole("button", { name: /이번 주 금요일/ })
        .first()
        .click();
      await expect(page.getByRole("article", { name: "메시지 초안" })).toBeVisible();
      await auditContrast(page, `${scheme}-tasks`);
      for (const [path, title] of TAB_SCREENS) {
        await page.goto(path);
        await expect(page.getByRole("heading", { name: title, level: 1 })).toBeVisible();
        await expect(page.getByText(/불러오는 중|확인하는 중/)).toHaveCount(0);
        await auditContrast(page, `${scheme}-${path.slice(1)}`);
      }
    });
  });
}

test.describe("theme-color", () => {
  test("밝음·어둠 theme-color와 apple-touch-icon, manifest 아이콘이 실제로 열린다", async ({
    page,
    request,
  }) => {
    await page.goto("/login");
    const themes = page.locator('meta[name="theme-color"]');
    await expect(themes).toHaveCount(2);
    await expect(themes.nth(0)).toHaveAttribute("media", "(prefers-color-scheme: light)");
    await expect(themes.nth(1)).toHaveAttribute("media", "(prefers-color-scheme: dark)");
    const apple = await page.locator('link[rel="apple-touch-icon"]').getAttribute("href");
    const manifest = await (await request.get("/manifest.webmanifest")).json();
    const sources = [apple, ...manifest.icons.map((icon: { src: string }) => icon.src)];
    for (const src of sources) {
      const response = await request.get(String(src));
      expect(response.status(), String(src)).toBe(200);
      expect(response.headers()["content-type"]).toBe("image/png");
    }
  });
});

// 시스템 설정을 따라 배경이 바뀌었는지(어둠: #0a0a0a, 밝음: #ffffff = theme-color와 같은 값).
// 계산된 색 글자(lab(…) 등)는 브라우저마다 달라 캔버스에 칠해 sRGB 값으로 비교한다.
async function expectBackground(page: Page, scheme: "dark" | "light") {
  const rgb = await page.evaluate(() => {
    const context = document.createElement("canvas").getContext("2d");
    if (!context) return [];
    context.fillStyle = getComputedStyle(document.body).backgroundColor;
    context.fillRect(0, 0, 1, 1);
    return [...context.getImageData(0, 0, 1, 1).data.slice(0, 3)];
  });
  expect(rgb).toEqual(scheme === "dark" ? [10, 10, 10] : [255, 255, 255]);
}

async function checkChat(page: Page, scheme: "dark" | "light") {
  await expectBackground(page, scheme);
  const list = page.getByRole("list", { name: "메시지 목록" });
  await expect(list.getByText(/찾아봤습니다/)).toBeVisible();
  await auditContrast(page, `${scheme}-chat-top`);
  await list
    .getByRole("button", { name: /찾는 중/ })
    .first()
    .click();
  await list.getByRole("button", { name: /메일 읽는 중/ }).click();
  await list.getByRole("article", { name: "메시지 초안" }).scrollIntoViewIfNeeded();
  await auditContrast(page, `${scheme}-chat-steps`);
  const cards = page.getByRole("article", { name: "승인 요청" });
  await cards.first().scrollIntoViewIfNeeded();
  await auditContrast(page, `${scheme}-chat-approval`);
  await page.getByRole("button", { name: "음성 입력" }).click();
  await expect(page.getByRole("heading", { name: "음성 입력 안내" })).toBeVisible();
  await auditContrast(page, `${scheme}-chat-voice`);
  await page.getByRole("button", { name: "취소", exact: true }).click();
  await page.getByRole("button", { name: "대화 목록" }).click();
  await expect(page.getByRole("navigation", { name: "대화 목록" })).toBeVisible();
  await auditContrast(page, `${scheme}-sheet`);
}
