import { expect, type Locator, type Page, test } from "@playwright/test";
import { loginToChat } from "./helpers";

// 6단계: 자동화 탭과 '예약 작업' 대화방의 [제안] 버튼이 360px에서 보이고 깨지지 않는지 본다.
// 예약·감시는 채팅의 모델 호출로만 생기고, 예약 실행 결과도 실제 시각이 되어야 생기므로
// 목록·켜기/끄기·삭제·메시지·전송 응답은 page.route로 흉내 낸다(실제 모델 호출 없음).

// 띄어쓰기 없는 긴 글자도 360px 안에서 줄을 바꾸는지 보려고 일부러 길게 만든다.
const LONG_WORD = "아주긴단어".repeat(30);

const BRIEFING = {
  id: 1,
  title: "아침 브리핑",
  prompt_text:
    "오늘 일정, 날씨, 미완료 할 일을 요약해라. 행동이 필요한 항목은 [제안] 줄로만 적어라.",
  time_of_day: "07:30",
  weekdays: [0, 1, 2, 3, 4, 5, 6],
  repeat_text: "매일 07:30",
  is_active: true,
  next_run_at: "2026-11-17T22:30:00Z",
  last_run_at: "2026-11-16T22:30:00Z",
  is_briefing: true,
  last_task: {
    id: 990301,
    conversation_id: 990001,
    status: "done",
    final_answer: `오늘 일정은 2개입니다. ${LONG_WORD}\n[제안] 우산 챙기기 할 일에 추가해줘`,
    error: null,
    updated_at: "2026-11-16T22:31:00Z",
  },
};

const WEEKLY = {
  ...BRIEFING,
  id: 2,
  title: "다음 주 일정 요약",
  prompt_text: "다음 주 일정 요약해줘",
  time_of_day: "21:00",
  weekdays: [6],
  repeat_text: "매주 일요일 21:00",
  is_active: false,
  last_run_at: null,
  is_briefing: false,
  last_task: null,
};

const RAIN = {
  id: 9,
  description: "내일 서울 비 확률",
  tool_name: "get_weather",
  tool_input: { place: "서울" },
  condition_text: "강수 확률이 60%를 넘으면",
  interval_minutes: 60,
  repeat: false,
  is_active: true,
  next_run_at: "2026-11-17T01:00:00Z",
  expires_at: "2099-12-17T00:00:00Z",
  last_notified_at: null,
  last_result: {
    status: "not_met",
    checked_at: "2026-11-17T00:00:00Z",
    summary: `강수 확률 30%로 조건에 맞지 않습니다. ${LONG_WORD}`,
    error: null,
    sources: [`https://open-meteo.com/${LONG_WORD}`],
  },
};

type FakeItem = { id: number; is_active: boolean };
type Fake = { schedules: FakeItem[]; watches: FakeItem[]; patches: number; deletes: number };

function idFrom(url: string): number {
  return Number(new URL(url).pathname.split("/").pop());
}

async function fakeAutomations(page: Page): Promise<Fake> {
  const server: Fake = { schedules: [BRIEFING, WEEKLY], watches: [RAIN], patches: 0, deletes: 0 };
  await page.route("**/api/schedules", (route) =>
    route.fulfill({ status: 200, json: { schedules: server.schedules, max_active: 10 } }),
  );
  await page.route("**/api/watches", (route) =>
    route.fulfill({ status: 200, json: { watches: server.watches, max_active: 10 } }),
  );
  await page.route("**/api/schedules/*", async (route) => {
    const id = idFrom(route.request().url());
    if (route.request().method() === "DELETE") {
      server.deletes += 1;
      server.schedules = server.schedules.filter((item) => item.id !== id);
      return route.fulfill({ status: 204 });
    }
    server.patches += 1;
    const active = (route.request().postDataJSON() as { is_active: boolean }).is_active;
    server.schedules = server.schedules.map((item) =>
      item.id === id ? { ...item, is_active: active } : item,
    );
    return route.fulfill({ status: 200, json: server.schedules.find((item) => item.id === id) });
  });
  await page.route("**/api/watches/*", (route) => {
    server.deletes += 1;
    const id = idFrom(route.request().url());
    server.watches = server.watches.filter((item) => item.id !== id);
    return route.fulfill({ status: 204 });
  });
  return server;
}

// 로그인 → 하단 탭 '자동화'로 들어간다(실제 사용자의 경로).
async function openAutomation(page: Page): Promise<Locator> {
  await loginToChat(page);
  await page
    .getByRole("navigation", { name: "하단 탭" })
    .getByRole("link", { name: "자동화" })
    .click();
  await expect(page).toHaveURL(/\/automation$/);
  const region = page.getByRole("region", { name: "자동화" });
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

function item(region: Locator, title: string): Locator {
  return region
    .getByRole("listitem")
    .filter({ has: region.page().getByRole("heading", { name: title }) });
}

test("360px 자동화 화면: 예약·감시 목록과 마지막 결과, 개수, 안내를 보여 준다", async ({
  page,
}) => {
  await fakeAutomations(page);
  const region = await openAutomation(page);

  await expect(region).toContainText("자동 작업은 조회만 합니다");
  await expect(region).toContainText("켜진 예약 작업 1개 / 최대 10개");
  await expect(region).toContainText("켜진 조건 감시 1개 / 최대 10개");
  const briefing = item(region, "아침 브리핑");
  await expect(briefing).toContainText("아침 브리핑");
  await expect(briefing).toContainText("매일 07:30");
  await expect(briefing).toContainText("다음 실행 11월 18일(수) 07:30");
  const result = briefing.getByRole("link", { name: "결과 보기" });
  await expect(result).toHaveAttribute("href", "/chat/990001");
  await expectTappable(result);
  await expectTappable(briefing.getByRole("switch", { name: "켜기·끄기" }));
  await expectTappable(briefing.getByRole("button", { name: "삭제" }));

  const rain = item(region, "내일 서울 비 확률");
  await expect(rain).toContainText("1시간마다 확인");
  await expect(rain).toContainText("조건에 맞지 않음");
  const source = rain.getByRole("link", { name: /open-meteo\.com/ });
  await expect(source).toHaveAttribute("target", "_blank");
  await expect(source).toHaveAttribute("rel", "noopener noreferrer");
  await expectTappable(rain.getByRole("switch", { name: "켜기·끄기" }));
  await expectNoHorizontalScroll(page);
});

test("360px 자동화 화면: 스위치로 끄면 PATCH 한 번, 삭제는 화면 안에서 한 번 묻는다", async ({
  page,
}) => {
  const server = await fakeAutomations(page);
  const region = await openAutomation(page);

  const briefing = item(region, "아침 브리핑");
  const toggle = briefing.getByRole("switch", { name: "켜기·끄기" });
  await expect(toggle).toHaveAttribute("aria-checked", "true");
  await toggle.click();
  await expect(toggle).toHaveAttribute("aria-checked", "false");
  await expect(region.getByRole("status")).toContainText("예약 작업을 껐습니다");
  await expect(briefing).toContainText("꺼져 있어 실행하지 않습니다");
  expect(server.patches).toBe(1);

  const rain = item(region, "내일 서울 비 확률");
  await rain.getByRole("button", { name: "삭제" }).click();
  await expect(rain).toContainText("이 조건 감시를 지울까요?");
  expect(server.deletes).toBe(0);
  const confirm = rain.getByRole("button", { name: "지우기" });
  await expectTappable(confirm);
  await expectTappable(rain.getByRole("button", { name: "취소" }));
  await expectNoHorizontalScroll(page);

  await confirm.click();
  await expect(region.getByRole("heading", { name: "내일 서울 비 확률" })).toHaveCount(0);
  await expect(region).toContainText("아직 조건 감시가 없습니다.");
  await expect(region).toContainText("내일 서울에 비 올 확률이 60% 넘으면 알려줘");
  expect(server.deletes).toBe(1);
});

// ---- '예약 작업' 대화방 ----

const ROOM_ID = 990001;
const SCHEDULE_TASK_ID = 990301;
const CHAT_TASK_ID = 990302;
const SUGGESTION = "우산 챙기기 할 일에 추가해줘";
// 감시 알림의 출처(서버 messages.sources). http·https만 링크가 된다.
const WATCH_SOURCES = ["https://open-meteo.com/", "javascript:alert(1)"];

type Room = { posts: string[] };

function roomMessages(room: Room) {
  const messages: object[] = [
    {
      id: 1,
      role: "assistant",
      content: BRIEFING.last_task.final_answer,
      task_id: SCHEDULE_TASK_ID,
      created_at: "2026-11-16T22:31:00Z",
      origin: "schedule",
      sources: [],
    },
    {
      id: 2,
      role: "assistant",
      content: "[조건 감시] 내일 서울 비 확률\n[제안] 감시 알림 안의 제안",
      task_id: null,
      created_at: "2026-11-16T23:00:00Z",
      origin: "watch",
      sources: WATCH_SOURCES,
    },
  ];
  for (const content of room.posts) {
    messages.push({
      id: 3,
      role: "user",
      content,
      task_id: CHAT_TASK_ID,
      created_at: "2026-11-17T00:00:00Z",
      origin: "chat",
      sources: [],
    });
    messages.push({
      id: 4,
      role: "assistant",
      content: "할 일에 추가했습니다.",
      task_id: CHAT_TASK_ID,
      created_at: "2026-11-17T00:00:05Z",
      origin: "chat",
      sources: [],
    });
  }
  return messages;
}

function doneTask(id: number, origin: string) {
  return {
    id,
    conversation_id: ROOM_ID,
    origin,
    status: "done",
    final_answer: "끝",
    error: null,
    request_count: 1,
    total_tokens: 10,
    created_at: "2026-11-16T22:30:00Z",
    updated_at: "2026-11-16T22:31:00Z",
    steps: [],
    approvals: [],
  };
}

async function fakeRoom(page: Page): Promise<Room> {
  const room: Room = { posts: [] };
  await page.route("**/api/conversations", (route) => {
    if (route.request().method() !== "GET") return route.continue();
    const conversation = {
      id: ROOM_ID,
      title: "예약 작업",
      created_at: "2026-11-16T22:30:00Z",
      updated_at: "2026-11-16T23:00:00Z",
    };
    return route.fulfill({ status: 200, json: [conversation] });
  });
  await page.route(`**/api/conversations/${ROOM_ID}/messages`, (route) => {
    if (route.request().method() === "POST") {
      room.posts.push((route.request().postDataJSON() as { content: string }).content);
      return route.fulfill({ status: 202, json: { task_id: CHAT_TASK_ID } });
    }
    return route.fulfill({ status: 200, json: roomMessages(room) });
  });
  await page.route(
    (url) => url.pathname === "/api/tasks",
    (route) => route.fulfill({ status: 200, json: [] }),
  );
  await page.route(`**/api/tasks/${SCHEDULE_TASK_ID}`, (route) =>
    route.fulfill({ status: 200, json: doneTask(SCHEDULE_TASK_ID, "schedule") }),
  );
  await page.route(`**/api/tasks/${CHAT_TASK_ID}`, (route) =>
    route.fulfill({ status: 200, json: doneTask(CHAT_TASK_ID, "chat") }),
  );
  return room;
}

test("360px '예약 작업' 대화방: 예약 결과의 [제안]만 버튼이 되고, 누를 때만 메시지 1건을 보낸다", async ({
  page,
}) => {
  await loginToChat(page);
  const room = await fakeRoom(page);
  await page.goto(`/chat/${ROOM_ID}`);
  const list = page.getByRole("list", { name: "메시지 목록" });

  await expect(list).toContainText("예약 작업 결과");
  await expect(list).toContainText("조건 감시 알림");
  const chip = list.getByRole("button", { name: SUGGESTION });
  await expectTappable(chip);
  await expect(list.getByRole("button", { name: "감시 알림 안의 제안" })).toHaveCount(0);
  const sources = list.getByRole("list", { name: "출처" });
  const link = sources.getByRole("link", { name: "https://open-meteo.com/" });
  await expect(link).toHaveAttribute("target", "_blank");
  await expect(link).toHaveAttribute("rel", "noopener noreferrer");
  await expect(sources).toContainText("javascript:alert(1)");
  await expect(sources.getByRole("link")).toHaveCount(1);
  await expectNoHorizontalScroll(page);
  expect(room.posts).toEqual([]); // 저절로 보내지 않는다

  await chip.click();
  await expect(list.getByText("할 일에 추가했습니다.")).toBeVisible();
  await expect(chip).toBeDisabled();
  expect(room.posts).toEqual([SUGGESTION]);
});
