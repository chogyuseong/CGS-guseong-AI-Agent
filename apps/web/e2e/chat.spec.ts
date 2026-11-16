import { expect, type Page, type Route, test } from "@playwright/test";
import { loginToChat } from "./helpers";

// 주의: 실제 서버에 메시지를 보내면 유료 모델 호출이 일어난다.
// 그래서 메시지 전송(POST)과 그 뒤의 메시지·task 조회는 page.route로 가짜 응답을 준다.
// 로그인과 대화 목록·생성처럼 모델을 부르지 않는 요청만 실제 서버로 간다.
const FAKE_TASK_ID = 900001;
const REQUEST_TEXT = "우유 사기 할 일에 추가해줘";
const ANSWER_TEXT = "앱 안의 할 일 목록에 '우유 사기'를 추가했습니다.";

const fakeTask = {
  id: FAKE_TASK_ID,
  conversation_id: 1,
  origin: "chat",
  status: "done",
  final_answer: ANSWER_TEXT,
  error: null,
  request_count: 2,
  total_tokens: 1200,
  created_at: "2026-11-16T12:00:00Z",
  updated_at: "2026-11-16T12:00:03Z",
  steps: [
    {
      id: 1,
      order: 1,
      tool_name: "add_todo",
      label: "할 일 추가: 우유 사기",
      risk_level: "INTERNAL",
      status: "done",
      ok: true,
      input: { title: "우유 사기" },
      output: { ok: true, data: { id: 1, title: "우유 사기" } },
      duration_ms: 15,
    },
  ],
  approvals: [],
};

function fakeMessages(sent: boolean) {
  if (!sent) return [];
  return [
    {
      id: 1,
      role: "user",
      content: REQUEST_TEXT,
      task_id: FAKE_TASK_ID,
      created_at: "2026-11-16T12:00:00Z",
      origin: "chat",
      sources: [],
    },
    {
      id: 2,
      role: "assistant",
      content: ANSWER_TEXT,
      task_id: FAKE_TASK_ID,
      created_at: "2026-11-16T12:00:03Z",
      origin: "chat",
      sources: [],
    },
  ];
}

// 메시지 API를 통째로 가짜로 바꾼다. route.continue()를 부르지 않으므로 실제 서버로는 절대 가지 않는다.
async function fakeChatApi(page: Page): Promise<{ postCount: () => number }> {
  let posts = 0;
  await page.route("**/api/conversations/*/messages", async (route: Route) => {
    if (route.request().method() === "POST") {
      posts += 1;
      await new Promise((resolve) => setTimeout(resolve, 500)); // 서버가 몇 초 걸리는 것을 흉내
      return route.fulfill({ status: 202, json: { task_id: FAKE_TASK_ID } });
    }
    return route.fulfill({ status: 200, json: fakeMessages(posts > 0) });
  });
  await page.route(`**/api/tasks/${FAKE_TASK_ID}`, (route) =>
    route.fulfill({ status: 200, json: fakeTask }),
  );
  return { postCount: () => posts };
}

test("360px에서 가짜 응답으로 메시지를 보내면 내 메시지, 답변, 단계 한 줄이 보인다", async ({
  page,
}) => {
  const api = await fakeChatApi(page);
  await loginToChat(page);

  const input = page.getByLabel("메시지 입력");
  await input.fill(REQUEST_TEXT);
  await page.getByRole("button", { name: "전송" }).click();

  // 전송 중에는 입력이 막히고 진행 중 표시가 보인다.
  await expect(input).toBeDisabled();
  await expect(page.getByRole("status")).toHaveText("답변을 기다리는 중…");

  const list = page.getByRole("list", { name: "메시지 목록" });
  await expect(list.getByText(REQUEST_TEXT)).toBeVisible();
  await expect(list.getByText(ANSWER_TEXT)).toBeVisible();

  const step = page.getByRole("button", { name: /할 일 추가: 우유 사기/ });
  await expect(step).toBeInViewport();
  await expect(step.getByRole("img", { name: "완료" })).toBeVisible();

  // 단계를 누르면 입력과 출력이 펼쳐진다.
  await step.click();
  await expect(page.getByText(/"title": "우유 사기"/).first()).toBeVisible();

  await expect(input).toBeEnabled();
  await expect(input).toHaveValue("");
  expect(api.postCount()).toBe(1);
});
