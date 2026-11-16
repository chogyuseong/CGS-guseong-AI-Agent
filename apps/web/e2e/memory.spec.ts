import { expect, type Locator, type Page, test } from "@playwright/test";
import { loginToChat } from "./helpers";

// 5단계: 기억 탭이 360px에서 보이고 깨지지 않는지 본다.
// 기억 목록(GET /api/memories)과 삭제(DELETE)는 page.route로 흉내 낸다(실제 기억은 채팅의 모델 호출로만 생겨서).
type FakeMemory = {
  id: number;
  content: string;
  created_at: string;
  source: {
    message_id: number;
    conversation_id: number;
    task_id: number | null;
    content: string;
    created_at: string;
  };
};

// 띄어쓰기 없는 긴 글자도 360px 안에서 줄을 바꾸는지 보려고 일부러 길게 만든다.
const LONG_WORD = "아주긴단어".repeat(30);

const MEMORIES: FakeMemory[] = [
  {
    id: 1,
    content: "매운 음식을 못 먹는다",
    created_at: "2026-11-16T16:30:00Z",
    source: {
      message_id: 10,
      conversation_id: 7,
      task_id: 100,
      content: "나 매운 거 못 먹어, 기억해",
      created_at: "2026-11-16T16:29:00Z",
    },
  },
  {
    id: 2,
    content: `좋아하는 말: ${LONG_WORD}`,
    created_at: "2026-11-15T01:00:00Z",
    source: {
      message_id: 20,
      conversation_id: 8,
      task_id: 200,
      content: `${LONG_WORD}\n<b>이것도</b> 기억해`,
      created_at: "2026-11-15T00:59:00Z",
    },
  },
];

async function fakeMemories(page: Page, initial: FakeMemory[]) {
  const server = { memories: [...initial], deleteAll: 0, deleteOne: 0 };
  await page.route("**/api/memories", (route) => {
    const method = route.request().method();
    if (method === "GET")
      return route.fulfill({ status: 200, json: { memories: server.memories, limit: 200 } });
    if (method !== "DELETE") return route.abort();
    server.deleteAll += 1;
    const deleted = server.memories.length;
    server.memories = [];
    return route.fulfill({ status: 200, json: { deleted } });
  });
  await page.route("**/api/memories/*", (route) => {
    if (route.request().method() !== "DELETE") return route.abort();
    server.deleteOne += 1;
    const id = Number(new URL(route.request().url()).pathname.split("/").pop());
    server.memories = server.memories.filter((memory) => memory.id !== id);
    return route.fulfill({ status: 204 });
  });
  return server;
}

// 로그인 → 하단 탭 '기억'으로 들어간다(실제 사용자의 경로).
async function openMemory(page: Page): Promise<Locator> {
  await loginToChat(page);
  await page
    .getByRole("navigation", { name: "하단 탭" })
    .getByRole("link", { name: "기억" })
    .click();
  await expect(page).toHaveURL(/\/memory$/);
  const region = page.getByRole("region", { name: "기억" });
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

test("360px 기억 화면: 목록과 개수를 보여 주고, 원래 메시지를 펼치면 내 메시지와 대화 링크가 보인다", async ({
  page,
}) => {
  await fakeMemories(page, MEMORIES);
  const region = await openMemory(page);

  await expect(region).toContainText("저장된 기억 2개 / 최대 200개");
  await expect(region).toContainText("지운 기억은 다음 대화부터 에이전트가 쓰지 않습니다");
  const first = region.getByRole("listitem").filter({ hasText: "매운 음식을 못 먹는다" });
  await expect(first).toContainText("저장한 날짜 2026년 11월 17일");
  const toggle = first.getByRole("button", { name: "원래 메시지 보기" });
  await expectTappable(toggle);
  await expectTappable(first.getByRole("button", { name: "삭제", exact: true }));

  await toggle.click();
  await expect(first).toContainText("나 매운 거 못 먹어, 기억해");
  const link = first.getByRole("link", { name: "이 대화로 가기" });
  await expect(link).toHaveAttribute("href", "/chat/7");
  await expectTappable(link);

  // 긴 글과 태그 모양의 글자도 360px 안에서 글자 그대로 보인다.
  const long = region.getByRole("listitem").filter({ hasText: "좋아하는 말" });
  await long.getByRole("button", { name: "원래 메시지 보기" }).click();
  await expect(long).toContainText("<b>이것도</b> 기억해");
  await expect(long.locator("b")).toHaveCount(0);
  await expectTappable(long.getByRole("button", { name: "원래 메시지 접기" }));
  await expectNoHorizontalScroll(page);
});

test("360px 기억 화면: 전체 삭제는 화면 안에서 한 번 묻고, 지우면 빈 상태 안내가 보인다", async ({
  page,
}) => {
  const server = await fakeMemories(page, MEMORIES);
  const region = await openMemory(page);

  const deleteAll = region.getByRole("button", { name: "전체 삭제" });
  await expectTappable(deleteAll);
  await deleteAll.click();
  await expect(region).toContainText("기억 2개를 모두 지울까요?");
  expect(server.deleteAll).toBe(0);
  const confirm = region.getByRole("button", { name: "모두 지우기" });
  await expectTappable(confirm);
  await expectTappable(region.getByRole("button", { name: "취소" }));
  await expectNoHorizontalScroll(page);

  await confirm.click();
  await expect(region).toContainText("아직 저장된 기억이 없습니다.");
  await expect(region.getByRole("status")).toContainText("기억 2개를 모두 지웠습니다");
  await expect(region).toContainText("'나 매운 거 못 먹어, 기억해'처럼 말하면 저장됩니다");
  await expect(region).toContainText("검색·날씨·장소·일정 조회를 쓴 대화에서는 저장되지 않습니다");
  await expect(region).toContainText("비밀번호·카드 번호는 저장하지 않습니다");
  await expect(region).toContainText("저장된 기억 0개 / 최대 200개");
  expect(server.deleteAll).toBe(1);
  await expectNoHorizontalScroll(page);
});

test("360px 기억 화면: 한 건 삭제는 그 기억만 지운다", async ({ page }) => {
  const server = await fakeMemories(page, MEMORIES);
  const region = await openMemory(page);

  const first = region.getByRole("listitem").filter({ hasText: "매운 음식을 못 먹는다" });
  await first.getByRole("button", { name: "삭제", exact: true }).click();

  await expect(region.getByText("매운 음식을 못 먹는다")).toHaveCount(0);
  await expect(region).toContainText("좋아하는 말");
  await expect(region).toContainText("저장된 기억 1개 / 최대 200개");
  expect(server.deleteOne).toBe(1);
});
