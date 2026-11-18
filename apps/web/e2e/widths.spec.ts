import { expect, type Page, test } from "@playwright/test";
import { loginToChat } from "./helpers";
import { auditScreen } from "./width-audit";
import { CHAT_ID, fakeChat, fakeTabs, LONG_SENTENCE, LONG_WORD, ROOM_ID } from "./width-fixtures";

// 9단계 1번: 모든 화면을 360·390·768·1280px에서 점검한다(가로 스크롤, 화면 밖 요소, 넘친 글자,
// 모바일 폭의 44px 버튼, 겹침, 넓은 화면의 최대 폭과 가운데 정렬).
// 다른 e2e는 360px 한 폭에서만 돌고, 폭을 바꿔 도는 것은 이 파일 하나다(전체 시간이 4배가 되지 않게).
// 데이터는 모두 page.route로 흉내 낸다(width-fixtures.ts). 로그인만 실제 API 서버로 간다.
// 스크린샷: WIDTH_SHOTS_DIR 환경 변수에 저장소 밖 폴더를 주면 화면마다 남긴다.
// 키보드가 올라온 상태의 입력창·승인 카드는 자동화로 볼 수 없어 docs/PHONE-CHECK.md에서 폰으로 본다.
const WIDTHS = [
  { width: 360, height: 740 },
  { width: 390, height: 844 },
  { width: 768, height: 1024 },
  { width: 1280, height: 800 },
];

for (const { width, height } of WIDTHS) {
  test.describe(`${width}px 폭 점검`, () => {
    const mobile = width < 768;
    test.use({
      viewport: { width, height },
      isMobile: mobile,
      hasTouch: mobile,
      deviceScaleFactor: 1,
    });

    test("로그인 화면", async ({ page }) => {
      await page.goto("/login");
      await expect(page.getByRole("button", { name: "로그인" })).toBeVisible();
      await auditScreen(page, "login", width);
    });

    test("채팅: 답변·단계 타임라인·승인 카드·mismatch 확인·대화 목록·예약 작업 대화방", async ({
      page,
    }) => {
      await fakeChat(page);
      await loginToChat(page);
      await expect(page).toHaveURL(new RegExp(`/chat/${CHAT_ID}$`));
      await checkChat(page, width);
      await checkSheet(page, width);
      await checkRoom(page, width);
    });

    test("탭 화면: 작업 기록·할 일·자동화·기억·설정·연결·비용", async ({ page }) => {
      await fakeChat(page);
      await fakeTabs(page);
      await loginToChat(page);
      await checkTasks(page, width);
      for (const [path, title] of TAB_SCREENS) {
        await page.goto(path);
        await expect(page.getByRole("heading", { name: title, level: 1 })).toBeVisible();
        await expect(page.getByText(/불러오는 중|확인하는 중/)).toHaveCount(0);
        await auditScreen(page, path.slice(1), width);
      }
      await checkUsage30(page, width);
    });
  });
}

const TAB_SCREENS = [
  ["/todos", "할 일"],
  ["/automation", "자동화"],
  ["/memory", "기억"],
  ["/settings", "설정"],
  ["/connections", "외부 서비스 연결"],
  ["/usage", "비용"],
] as const;

// 비용 화면은 기간을 30일로 바꿔 가장 긴 표도 본다(TAB_SCREENS의 /usage 다음에 이어서).
async function checkUsage30(page: Page, width: number) {
  await page.getByRole("button", { name: "30일" }).click();
  await expect(page.getByRole("table").getByRole("row")).toHaveCount(31);
  await expect(page.getByText(/불러오는 중/)).toHaveCount(0);
  await auditScreen(page, "usage-30", width);
}

async function checkChat(page: Page, width: number) {
  const list = page.getByRole("list", { name: "메시지 목록" });
  await expect(list.getByText(/찾아봤습니다/)).toBeVisible();
  await auditScreen(page, "chat-top", width);
  // 첫 단계(웹 검색)와 메일 단계를 펼쳐 외부 콘텐츠 상자를 본다.
  await list
    .getByRole("button", { name: /찾는 중/ })
    .first()
    .click();
  await list.getByRole("button", { name: /메일 읽는 중/ }).click();
  const mailBox = list.getByRole("region", { name: "외부에서 가져온 내용" }).nth(1);
  await mailBox.scrollIntoViewIfNeeded();
  await expect(mailBox).toContainText("앞 500자만 보관");
  await auditScreen(page, "chat-steps", width);
  const cards = page.getByRole("article", { name: "승인 요청" });
  await expect(cards).toHaveCount(2);
  await cards.first().scrollIntoViewIfNeeded();
  await auditScreen(page, "chat-approval", width);
  await cards.first().getByRole("button", { name: "승인", exact: true }).click();
  const confirm = cards.first().getByRole("button", { name: "그래도 승인" });
  await confirm.scrollIntoViewIfNeeded();
  await expect(confirm).toBeInViewport({ ratio: 1 });
  await auditScreen(page, "chat-mismatch", width);
}

async function checkSheet(page: Page, width: number) {
  await page.getByRole("button", { name: "대화 목록" }).click();
  const nav = page.getByRole("navigation", { name: "대화 목록" });
  await expect(nav.getByRole("link")).toHaveCount(14);
  await auditScreen(page, "sheet", width);
  await nav.getByRole("link", { name: /예약 작업/ }).click();
}

async function checkRoom(page: Page, width: number) {
  await expect(page).toHaveURL(new RegExp(`/chat/${ROOM_ID}$`));
  const list = page.getByRole("list", { name: "메시지 목록" });
  // 6단계 보강: 제안 버튼(이름이 긴 단어)과 그 아래 긴 문장이 화면 안에서 줄을 바꾸는지 본다.
  await expect(
    list.getByRole("button", { name: `${LONG_WORD}: 입력창에 채우기`, exact: true }),
  ).toBeVisible();
  await expect(list.getByText(LONG_SENTENCE, { exact: true }).first()).toBeVisible();
  await auditScreen(page, "room", width);
  // 감시 알림 아래의 출처 링크(긴 URL 포함)도 화면 안에서 줄을 바꾸는지 본다.
  const sources = list.getByRole("list", { name: "출처" });
  await sources.scrollIntoViewIfNeeded();
  await expect(sources.getByRole("link")).toHaveCount(2);
  await auditScreen(page, "room-sources", width);
}

async function checkTasks(page: Page, width: number) {
  await page.goto("/tasks");
  await expect(page.getByRole("heading", { name: "작업 기록" })).toBeVisible();
  await page
    .getByRole("button", { name: /이번 주 금요일/ })
    .first()
    .click();
  await page.getByRole("button", { name: /메일 읽는 중/ }).click();
  await expect(page.getByText(/앞 500자만 보관/)).toBeVisible();
  await auditScreen(page, "tasks", width);
}
