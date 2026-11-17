import { expect, type Locator, type Page, type Route, test } from "@playwright/test";
import { loginToChat } from "./helpers";

// 테스트 15: 360px 폭에서 승인 카드의 승인·거절 버튼이 화면 안에 보이고 누를 수 있다.
//
// 운영 코드에는 아직 EXTERNAL 도구가 없고(4단계에 생김), 실제 메시지 전송은 유료 모델을 부른다.
// 그래서 chat.spec.ts처럼 메시지 전송·task·승인 응답을 page.route로 흉내 낸다(실제 서버로 보내지 않는다).
// 로그인과 대화 목록·생성처럼 모델을 부르지 않는 요청만 실제 서버로 간다.
const TASK_ID = 900101;
const APPROVAL_ID = 700101;
const REQUEST_TEXT = "금요일 저녁 7시에 저녁 약속 잡아줘";
const SUMMARY = "11월 20일(금) 19:00~21:00 '저녁 약속' 일정을 기본 캘린더에 만듭니다.";
const ANSWER_TEXT = "11월 20일(금) 19:00에 '저녁 약속' 일정을 만들었습니다.";

type Guard = { verdict: "match" | "mismatch" | "unsure"; reason: string };
type Phase = "idle" | "waiting" | "done";

function approval(status: "pending" | "approved", guard: Guard) {
  return {
    id: APPROVAL_ID,
    step_id: 1,
    tool_name: "create_event",
    tool_input: { title: "저녁 약속", start: "2026-11-20T19:00:00+09:00" },
    status,
    summary: SUMMARY,
    expires_at: new Date(Date.now() + 30 * 60_000).toISOString(),
    decided_at: status === "pending" ? null : new Date().toISOString(),
    guard,
    needs_mismatch_confirmation: guard.verdict === "mismatch",
    target: "Google 캘린더(기본 캘린더)",
    account: "연결된 Google 계정",
    execute_before: status === "pending" ? null : new Date(Date.now() + 5 * 60_000).toISOString(),
    operation_status: status === "pending" ? "awaiting_approval" : "succeeded",
    receipt_confirmed: status !== "pending",
  };
}

// conversationId는 실제 서버가 만든 대화 번호다. 화면은 이 값으로 메시지 목록을 다시 가져오므로 맞춰야 한다.
function task(phase: Phase, guard: Guard, conversationId: number) {
  const done = phase === "done";
  return {
    id: TASK_ID,
    conversation_id: conversationId,
    origin: "chat",
    status: done ? "done" : "waiting_approval",
    final_answer: done ? ANSWER_TEXT : null,
    error: null,
    request_count: done ? 2 : 1,
    total_tokens: 1500,
    created_at: "2026-11-17T10:00:00Z",
    updated_at: "2026-11-17T10:00:03Z",
    steps: [
      {
        id: 1,
        order: 1,
        tool_name: "create_event",
        label: "캘린더에 '저녁 약속' 만드는 중",
        risk_level: "EXTERNAL",
        status: done ? "done" : "waiting_approval",
        ok: done ? true : null,
        input: { title: "저녁 약속" },
        output: done ? { ok: true, data: { id: "evt1" } } : null,
        duration_ms: done ? 300 : null,
      },
    ],
    approvals: [approval(done ? "approved" : "pending", guard)],
  };
}

function messages(phase: Phase) {
  if (phase === "idle") return [];
  const user = {
    id: 1,
    role: "user",
    content: REQUEST_TEXT,
    task_id: TASK_ID,
    created_at: "2026-11-17T10:00:00Z",
    origin: "chat",
    sources: [],
  };
  if (phase === "waiting") return [user];
  const answer = {
    id: 2,
    role: "assistant",
    content: ANSWER_TEXT,
    task_id: TASK_ID,
    created_at: "2026-11-17T10:00:05Z",
    origin: "chat",
    sources: [],
  };
  return [user, answer];
}

// 메시지 전송 → 승인 대기 → 승인(재개 run이 몇 초 걸림) → 완료를 흉내 낸다.
async function fakeApprovalApi(page: Page, guard: Guard) {
  let phase: Phase = "idle";
  let conversationId = 0;
  const approveBodies: unknown[] = [];
  await page.route("**/api/conversations/*/messages", async (route: Route) => {
    conversationId = Number(/conversations\/(\d+)\/messages/.exec(route.request().url())?.[1]);
    if (route.request().method() === "POST") {
      phase = "waiting";
      return route.fulfill({ status: 202, json: { task_id: TASK_ID } });
    }
    return route.fulfill({ status: 200, json: messages(phase) });
  });
  await page.route(`**/api/tasks/${TASK_ID}`, (route) =>
    route.fulfill({ status: 200, json: task(phase, guard, conversationId) }),
  );
  await page.route(`**/api/approvals/${APPROVAL_ID}/approve`, async (route) => {
    approveBodies.push(route.request().postDataJSON());
    await new Promise((resolve) => setTimeout(resolve, 1500)); // 재개 run을 기다리는 시간
    phase = "done";
    const body = { approval: approval("approved", guard), changed: true, task_status: "done" };
    return route.fulfill({ status: 200, json: body });
  });
  return { approveBodies: () => approveBodies };
}

async function sendRequest(page: Page): Promise<Locator> {
  await loginToChat(page);
  await page.getByLabel("메시지 입력").fill(REQUEST_TEXT);
  await page.getByRole("button", { name: "전송" }).click();
  const card = page.getByRole("article", { name: "승인 요청" });
  await expect(card).toBeVisible();
  return card;
}

// 버튼이 360px 화면 안에 다 들어오고, 손가락으로 누를 만한 높이(44px 이상)인지 확인한다.
async function expectTappable(button: Locator): Promise<void> {
  await button.scrollIntoViewIfNeeded();
  await expect(button).toBeInViewport({ ratio: 1 });
  await expect(button).toBeEnabled();
  const box = await button.boundingBox();
  expect(box).not.toBeNull();
  if (!box) return;
  expect(box.height).toBeGreaterThanOrEqual(44);
  expect(box.x).toBeGreaterThanOrEqual(0);
  expect(box.x + box.width).toBeLessThanOrEqual(360);
}

test("테스트 15: 360px에서 승인 카드가 보이고, 승인하면 버튼이 막힌 뒤 최종 답변이 보인다", async ({
  page,
}) => {
  const api = await fakeApprovalApi(page, { verdict: "match", reason: "요청한 일정과 같습니다." });
  const card = await sendRequest(page);

  await expect(card).toContainText(SUMMARY);
  await expect(card).toContainText("AI 검토: 일치로 추정");
  await expect(card).toContainText("Google 캘린더(기본 캘린더)");
  await expect(card).toContainText(/분 남음/);
  const approve = card.getByRole("button", { name: "승인", exact: true });
  const reject = card.getByRole("button", { name: "거절", exact: true });
  await expectTappable(approve);
  await expectTappable(reject);
  // 가로 스크롤이 생기지 않는다(카드가 360px 안에 들어간다).
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(360);

  await approve.click();
  await expect(approve).toBeDisabled();
  await expect(reject).toBeDisabled();
  await expect(card.getByRole("status")).toContainText("승인 처리 중");

  const list = page.getByRole("list", { name: "메시지 목록" });
  await expect(list.getByText(ANSWER_TEXT)).toBeVisible();
  await expect(page.getByText("승인함")).toBeVisible();
  await expect(page.getByRole("button", { name: "승인", exact: true })).toHaveCount(0);
  expect(api.approveBodies()).toEqual([{ confirm_mismatch: false }]);
});

test("테스트 15(mismatch): 승인 전에 한 번 더 묻고, 확인하면 confirm_mismatch=true로 보낸다", async ({
  page,
}) => {
  const reason = "요청은 금요일인데 토요일 일정을 만듭니다.";
  const api = await fakeApprovalApi(page, { verdict: "mismatch", reason });
  const card = await sendRequest(page);

  await expect(card).toContainText("AI 검토: 요청과 다를 수 있음");
  await expect(card).toContainText(reason);

  await card.getByRole("button", { name: "승인", exact: true }).click();
  await expect(card).toContainText("요청과 다를 수 있습니다. 그래도 진행할까요?");
  expect(api.approveBodies()).toEqual([]);

  const confirm = card.getByRole("button", { name: "그래도 승인" });
  await expectTappable(confirm);
  await expectTappable(card.getByRole("button", { name: "취소" }));
  await confirm.click();
  await expect(confirm).toBeDisabled();

  await expect(
    page.getByRole("list", { name: "메시지 목록" }).getByText(ANSWER_TEXT),
  ).toBeVisible();
  expect(api.approveBodies()).toEqual([{ confirm_mismatch: true }]);
});
