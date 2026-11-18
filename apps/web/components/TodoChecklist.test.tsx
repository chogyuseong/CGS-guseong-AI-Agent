import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Todo } from "@/lib/todos";
import { TodoChecklist } from "./TodoChecklist";

// 서버 호출(api.GET / api.PUT)을 가짜로 바꾼다. 가짜 서버는 할 일 목록을 들고 있다가 PUT이 오면 상태를 바꾼다.
// 할 일 체크리스트(요구사항 밖 기능, DECISIONS 2026-11-18)를 확인한다.
const getMock = vi.fn();
const putMock = vi.fn();
vi.mock("@/lib/api/client", () => ({
  api: {
    GET: (...args: unknown[]) => getMock(...args),
    PUT: (...args: unknown[]) => putMock(...args),
  },
}));

function makeTodo(id: number, title: string, isDone = false): Todo {
  return {
    id,
    title,
    is_done: isDone,
    done_at: isDone ? "2026-11-18T02:00:00Z" : null,
    created_at: "2026-11-18T01:00:00Z",
  };
}

type PutOptions = { params: { path: { todo_id: number } }; body: { is_done: boolean } };

function serve(initial: Todo[], putStatus = 200) {
  let todos = [...initial];
  getMock.mockImplementation(async () => ({
    data: { todos },
    response: { status: 200, ok: true },
  }));
  putMock.mockImplementation(async (_path: string, options: PutOptions) => {
    if (putStatus !== 200) return { data: undefined, response: { status: putStatus, ok: false } };
    const id = options.params.path.todo_id;
    todos = todos.map((todo) =>
      todo.id === id ? makeTodo(id, todo.title, options.body.is_done) : todo,
    );
    const changed = todos.find((todo) => todo.id === id);
    return { data: changed, response: { status: 200, ok: true } };
  });
}

function renderChecklist() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <TodoChecklist />
    </QueryClientProvider>,
  );
}

describe("TodoChecklist", () => {
  beforeEach(() => {
    getMock.mockReset();
    putMock.mockReset();
  });

  it("체크하면 서버에 완료로 보내고 체크된 상태로 보인다", async () => {
    serve([makeTodo(1, "우유 사기"), makeTodo(2, "달걀 사기")]);
    renderChecklist();

    const milk = await screen.findByRole("checkbox", { name: "우유 사기" });
    expect(milk).not.toBeChecked();
    fireEvent.click(milk);

    await waitFor(() => expect(screen.getByRole("checkbox", { name: "우유 사기" })).toBeChecked());
    expect(putMock).toHaveBeenCalledWith("/api/todos/{todo_id}", {
      params: { path: { todo_id: 1 } },
      body: { is_done: true },
    });
    expect(screen.getByRole("checkbox", { name: "달걀 사기" })).not.toBeChecked();
  });

  it("완료한 것을 다시 누르면 체크를 해제한다", async () => {
    serve([makeTodo(3, "빵 사기", true)]);
    renderChecklist();

    fireEvent.click(await screen.findByRole("checkbox", { name: "빵 사기" }));

    await waitFor(() =>
      expect(screen.getByRole("checkbox", { name: "빵 사기" })).not.toBeChecked(),
    );
    expect(putMock).toHaveBeenCalledWith("/api/todos/{todo_id}", {
      params: { path: { todo_id: 3 } },
      body: { is_done: false },
    });
  });

  it("할 일이 없으면 채팅으로 추가하라고 안내한다", async () => {
    serve([]);
    renderChecklist();

    expect(await screen.findByText(/채팅에서 "우유 사기 할 일에 추가해 줘"/)).toBeTruthy();
  });

  it("바꾸지 못하면 오류 문구를 보여 준다", async () => {
    serve([makeTodo(1, "우유 사기")], 500);
    renderChecklist();

    fireEvent.click(await screen.findByRole("checkbox", { name: "우유 사기" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("할 일을 바꾸지 못했습니다");
  });
});
