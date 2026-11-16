import { expect, type Page, type Route, test } from "@playwright/test";
import { loginToChat } from "./helpers";

// 3단계: 서버가 메시지 전송·승인에 바로 답하고(202), 화면은 task 폴링으로 진행을 보여 준다.
// 실제 서버로 메시지를 보내면 유료 모델 호출이 일어나므로 메시지·task·승인 응답은 page.route로 흉내 낸다.
// 테스트 코드가 phase 값을 바꾸면 다음 폴링부터 서버 상태가 바뀐 것처럼 보인다.
const TASK_ID = 900201;
const APPROVAL_ID = 700201;
const REQUEST_TEXT = "할 일 세 개 추가해줘";
const ANSWER_TEXT = "할 일 세 개를 앱 안의 할 일 목록에 추가했습니다.";
const STEP_LABEL = "할 일에 '우유 사기' 추가하는 중";

type Phase = "idle" | "queued" | "running" | "waiting" | "resuming" | "done";
type Server = { phase: Phase; conversationId: number };

const STATUS_BY_PHASE: Record<Exclude<Phase, "idle">, string> = {
  queued: "queued",
  running: "running",
  waiting: "waiting_approval",
  resuming: "waiting_approval",
  done: "done",
};

function step(phase: Phase, withApproval: boolean) {
  const done = phase === "done";
  const waiting = withApproval && (phase === "waiting" || phase === "resuming");
  return {
    id: 1,
    order: 1,
    tool_name: withApproval ? "create_event" : "add_todo",
    label: STEP_LABEL,
    risk_level: withApproval ? "EXTERNAL" : "INTERNAL",
    status: done ? "done" : waiting ? "waiting_approval" : "running",
    ok: done ? true : null,
    input: { title: "우유 사기" },
    output: done ? { ok: true } : null,
    duration_ms: done ? 20 : null,
  };
}

function approval(phase: Phase) {
  const pending = phase === "waiting";
  return {
    id: APPROVAL_ID,
    step_id: 1,
    tool_name: "create_event",
    tool_input: { title: "우유 사기" },
    status: pending ? "pending" : "approved",
    summary: "11월 20일(금) 19:00 '우유 사기' 일정을 만듭니다.",
    expires_at: new Date(Date.now() + 30 * 60_000).toISOString(),
    decided_at: pending ? null : new Date().toISOString(),
    guard: { verdict: "match", reason: "요청과 같습니다." },
    needs_mismatch_confirmation: false,
  };
}

function task(server: Server, withApproval: boolean) {
  const phase = server.phase === "idle" ? "queued" : server.phase;
  const started = phase !== "queued";
  return {
    id: TASK_ID,
    conversation_id: server.conversationId,
    origin: "chat",
    status: STATUS_BY_PHASE[phase],
    final_answer: phase === "done" ? ANSWER_TEXT : null,
    error: null,
    request_count: 1,
    total_tokens: 100,
    created_at: "2026-11-17T10:00:00Z",
    updated_at: "2026-11-17T10:00:01Z",
    steps: started ? [step(phase, withApproval)] : [],
    approvals: withApproval && started ? [approval(phase)] : [],
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
  if (phase !== "done") return [user];
  return [
    user,
    {
      id: 2,
      role: "assistant",
      content: ANSWER_TEXT,
      task_id: TASK_ID,
      created_at: "2026-11-17T10:00:09Z",
      origin: "chat",
      sources: [],
    },
  ];
}

async function fakeApi(page: Page, withApproval: boolean): Promise<Server> {
  const server: Server = { phase: "idle", conversationId: 0 };
  await page.route("**/api/conversations/*/messages", async (route: Route) => {
    server.conversationId = Number(
      /conversations\/(\d+)\/messages/.exec(route.request().url())?.[1],
    );
    if (route.request().method() === "POST") {
      server.phase = "queued"; // 3단계 서버: 실행을 기다리지 않고 바로 202
      return route.fulfill({ status: 202, json: { task_id: TASK_ID } });
    }
    return route.fulfill({ status: 200, json: messages(server.phase) });
  });
  await page.route(`**/api/tasks/${TASK_ID}`, (route) =>
    route.fulfill({ status: 200, json: task(server, withApproval) }),
  );
  await page.route(`**/api/approvals/${APPROVAL_ID}/approve`, (route) => {
    // 3단계 서버: 재개 워크플로를 시작만 하고 바로 응답한다(task는 아직 waiting_approval).
    server.phase = "resuming";
    const body = { approval: approval("resuming"), changed: true, task_status: "waiting_approval" };
    return route.fulfill({ status: 200, json: body });
  });
  return server;
}

async function send(page: Page) {
  await loginToChat(page);
  await page.getByLabel("메시지 입력").fill(REQUEST_TEXT);
  await page.getByRole("button", { name: "전송" }).click();
}

test("즉시 202 뒤 대기→진행→완료를 폴링으로 보여 주고, 새로 고침해도 진행 상황이 이어진다", async ({
  page,
}) => {
  const server = await fakeApi(page, false);
  await send(page);
  const list = page.getByRole("list", { name: "메시지 목록" });

  await expect(list.getByText(REQUEST_TEXT)).toBeVisible();
  await expect(list.getByText(/곧 시작합니다/)).toBeVisible();
  // 바로 응답했으므로 입력창은 곧 다시 쓸 수 있다.
  await expect(page.getByLabel("메시지 입력")).toBeEnabled();

  server.phase = "running";
  await expect(list.getByText("작업하는 중…")).toBeVisible();
  const stepButton = list.getByRole("button", { name: new RegExp(STEP_LABEL) });
  await expect(stepButton.getByRole("img", { name: "진행 중" })).toBeVisible();

  // 화면을 닫았다 연 것처럼 새로 고침해도 서버의 task에서 진행 상황을 다시 읽는다.
  await page.reload();
  await expect(list.getByText(REQUEST_TEXT)).toBeVisible();
  await expect(list.getByText("작업하는 중…")).toBeVisible();
  await expect(stepButton).toBeVisible();

  server.phase = "done";
  await expect(list.getByText(ANSWER_TEXT)).toBeVisible();
  await expect(list.getByText("작업하는 중…")).toHaveCount(0);
  await expect(stepButton.getByRole("img", { name: "완료" })).toBeVisible();
});

test("승인이 바로 응답해도 재개가 끝날 때까지 '이어서 처리하는 중'이 이어진다", async ({
  page,
}) => {
  const server = await fakeApi(page, true);
  await send(page);
  server.phase = "waiting";
  const card = page.getByRole("article", { name: "승인 요청" });
  await expect(card).toBeVisible();

  await card.getByRole("button", { name: "승인", exact: true }).click();

  const list = page.getByRole("list", { name: "메시지 목록" });
  await expect(card).toContainText("승인함");
  await expect(list.getByText(/이어서 처리하는 중/)).toBeVisible();
  await expect(list.getByText(/승인이 필요한 작업이 있습니다/)).toHaveCount(0);

  server.phase = "running";
  await expect(list.getByText("작업하는 중…")).toBeVisible();

  server.phase = "done";
  await expect(list.getByText(ANSWER_TEXT)).toBeVisible();
  await expect(list.getByText(/이어서 처리하는 중|작업하는 중/)).toHaveCount(0);
});
