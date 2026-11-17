import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Approval } from "@/lib/approvals";
import type { Task, TaskStatus } from "@/lib/chat";
import { PendingTask } from "./PendingTask";

// 서버 호출(api.GET)을 가짜로 바꾼다. 테스트마다 돌려줄 task를 차례로 정한다.
const getMock = vi.fn();
const postMock = vi.fn();
vi.mock("@/lib/api/client", () => ({
  api: {
    GET: (...args: unknown[]) => getMock(...args),
    POST: (...args: unknown[]) => postMock(...args),
  },
}));

function makeTask(status: TaskStatus, overrides: Partial<Task> = {}): Task {
  return {
    id: 41,
    conversation_id: 3,
    origin: "chat",
    status,
    final_answer: null,
    error: null,
    request_count: 1,
    total_tokens: 100,
    created_at: "2026-11-17T03:00:00Z",
    updated_at: "2026-11-17T03:00:01Z",
    steps: [],
    approvals: [],
    ...overrides,
  };
}

const runningStep = {
  id: 1,
  order: 1,
  tool_name: "add_todo",
  label: "할 일에 '우유 사기' 추가하는 중",
  risk_level: "INTERNAL" as const,
  status: "running" as const,
  ok: null,
  input: { title: "우유 사기" },
  output: null,
  duration_ms: null,
};

const approved: Approval = {
  id: 9,
  step_id: 1,
  tool_name: "create_event",
  tool_input: {},
  status: "approved",
  summary: "일정을 만듭니다.",
  expires_at: "2026-11-17T03:30:00Z",
  decided_at: "2026-11-17T03:01:00Z",
  guard: null,
  needs_mismatch_confirmation: false,
};

// 차례로 돌려줄 task 목록. 마지막 것은 계속 돌려준다.
function serveTasks(...tasks: Task[]) {
  let index = 0;
  getMock.mockImplementation(async (path: string) => {
    if (path !== "/api/tasks/{task_id}") return { data: [], response: { status: 200 } };
    const task = tasks[Math.min(index, tasks.length - 1)];
    index += 1;
    return { data: task, response: { status: 200 } };
  });
}

const taskCalls = () =>
  getMock.mock.calls.filter(([path]) => path === "/api/tasks/{task_id}").length;

function renderPending() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <PendingTask taskId={41} />
    </QueryClientProvider>,
  );
}

describe("PendingTask(즉시 응답 뒤 진행 표시)", () => {
  beforeEach(() => getMock.mockReset());

  it("대기 중(queued)이면 곧 시작한다고 보여 준다", async () => {
    serveTasks(makeTask("queued"));
    renderPending();
    expect(await screen.findByText(/곧 시작합니다/)).toBeInTheDocument();
  });

  it("진행 중이면 '작업하는 중'과 지금 단계를 보여 준다", async () => {
    serveTasks(makeTask("running", { steps: [runningStep] }));
    renderPending();
    expect(await screen.findByText("작업하는 중…")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /우유 사기/ })).toBeInTheDocument();
  });

  it("승인을 모두 결정했고 서버가 재개 전이면 '이어서 처리하는 중'을 보여 준다", async () => {
    serveTasks(makeTask("waiting_approval", { approvals: [approved] }));
    renderPending();
    expect(await screen.findByText(/이어서 처리하는 중/)).toBeInTheDocument();
    expect(screen.queryByText(/승인이 필요한 작업이 있습니다/)).not.toBeInTheDocument();
  });

  it("진행 중에는 1초마다 다시 가져오고, 끝나면 멈춘다", async () => {
    serveTasks(makeTask("queued"), makeTask("running"), makeTask("done"));
    renderPending();

    // 처음 + 1초 뒤 + 2초 뒤 = 3번째 응답에서 done이 된다.
    await waitFor(() => expect(taskCalls()).toBe(3), { timeout: 3500 });
    await waitFor(() => expect(screen.queryByText("작업하는 중…")).not.toBeInTheDocument());
    await new Promise((resolve) => setTimeout(resolve, 1500));
    expect(taskCalls()).toBe(3);
  }, 8000);
});

describe("PendingTask 작업 취소(1단계 보강)", () => {
  beforeEach(() => {
    getMock.mockReset();
    postMock.mockReset();
  });

  it("진행 중이면 '작업 취소' 버튼이 있고, 누르면 취소를 요청한다", async () => {
    serveTasks(makeTask("running"));
    postMock.mockResolvedValue({
      data: { task_id: 41, status: "cancel_requested" },
      response: { status: 200 },
    });
    renderPending();

    fireEvent.click(await screen.findByRole("button", { name: "작업 취소" }));

    await waitFor(() =>
      expect(postMock).toHaveBeenCalledWith("/api/tasks/{task_id}/cancel", {
        params: { path: { task_id: 41 } },
      }),
    );
  });

  it("취소를 요청했으면 곧 멈춘다고 알리고 버튼은 없다", async () => {
    serveTasks(makeTask("cancel_requested"));
    renderPending();

    expect(await screen.findByText(/취소 요청을 받았습니다/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "작업 취소" })).not.toBeInTheDocument();
  });

  it("끝난 작업에는 취소 버튼이 없다", async () => {
    serveTasks(makeTask("done"));
    renderPending();

    await waitFor(() => expect(taskCalls()).toBeGreaterThan(0));
    expect(screen.queryByRole("button", { name: "작업 취소" })).not.toBeInTheDocument();
  });
});
