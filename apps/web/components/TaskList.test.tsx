import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { TaskSummary } from "@/lib/chat";
import { TaskList } from "./TaskList";

// 서버 호출(api.GET)을 가짜로 바꾼다. attention=true면 확인 필요 목록, 아니면 전체 목록을 돌려준다.
// 작업 기록 탭의 '확인 필요' 모아보기(DECISIONS 2026-11-18)를 확인한다.
const getMock = vi.fn();
vi.mock("@/lib/api/client", () => ({
  api: { GET: (...args: unknown[]) => getMock(...args) },
}));

function makeTask(id: number, status: TaskSummary["status"], request: string): TaskSummary {
  return {
    id,
    conversation_id: 1,
    origin: "chat",
    status,
    request_text: request,
    final_answer: null,
    error: null,
    step_count: 1,
    created_at: "2026-11-18T01:00:00Z",
    updated_at: "2026-11-18T01:00:00Z",
  };
}

const WAITING = makeTask(1, "waiting_approval", "회의 일정 만들어 줘");
const DONE = makeTask(2, "done", "날씨 알려 줘");

function serve(all: TaskSummary[], attention: TaskSummary[]) {
  getMock.mockImplementation(
    async (_path: string, options?: { params?: { query?: { attention?: boolean } } }) => ({
      data: options?.params?.query?.attention ? attention : all,
      response: { status: 200, ok: true },
    }),
  );
}

function renderList() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <TaskList />
    </QueryClientProvider>,
  );
}

describe("TaskList 확인 필요 모아보기", () => {
  beforeEach(() => getMock.mockReset());

  it("확인 필요 건수를 보여 주고 누르면 그 작업만 남긴다", async () => {
    serve([DONE, WAITING], [WAITING]);
    renderList();

    const filter = await screen.findByRole("button", { name: "확인 필요 1건" });
    expect(screen.getByText("날씨 알려 줘")).toBeTruthy();

    fireEvent.click(filter);

    await waitFor(() => expect(screen.queryByText("날씨 알려 줘")).toBeNull());
    expect(screen.getByText("회의 일정 만들어 줘")).toBeTruthy();
    expect(filter.getAttribute("aria-pressed")).toBe("true");
  });

  it("확인할 작업이 없으면 0건을 보여 주고 눌러도 빈 안내를 띄운다", async () => {
    serve([DONE], []);
    renderList();

    fireEvent.click(await screen.findByRole("button", { name: "확인 필요 0건" }));

    expect(await screen.findByText("지금 확인할 작업이 없습니다.")).toBeTruthy();
  });
});
