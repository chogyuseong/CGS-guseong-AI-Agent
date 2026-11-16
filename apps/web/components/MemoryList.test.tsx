import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Memory } from "@/lib/memories";
import { MemoryList } from "./MemoryList";

// 서버 호출(api.GET / api.DELETE)을 가짜로 바꾼다. 가짜 서버는 기억 목록을 들고 있다가 삭제하면 줄인다.
const getMock = vi.fn();
const deleteMock = vi.fn();
vi.mock("@/lib/api/client", () => ({
  api: {
    GET: (...args: unknown[]) => getMock(...args),
    DELETE: (...args: unknown[]) => deleteMock(...args),
  },
}));

function makeMemory(id: number, content: string, sourceText = `${content}, 기억해`): Memory {
  return {
    id,
    content,
    created_at: "2026-11-16T16:30:00Z",
    source: {
      message_id: id * 10,
      conversation_id: 7,
      task_id: id * 100,
      content: sourceText,
      created_at: "2026-11-16T16:29:00Z",
    },
  };
}

const SPICY = makeMemory(1, "매운 음식을 못 먹는다", "나 매운 거 못 먹어, 기억해");
const COFFEE = makeMemory(2, "아침에 커피를 마신다");

type FakeServer = { memories: Memory[]; deleteOne: number; deleteAll: number };

function serve(initial: Memory[]): FakeServer {
  const server: FakeServer = { memories: [...initial], deleteOne: 0, deleteAll: 0 };
  getMock.mockImplementation(async () => ({
    data: { memories: server.memories, limit: 200 },
    response: { status: 200, ok: true },
  }));
  deleteMock.mockImplementation(
    async (path: string, options?: { params: { path: { memory_id: number } } }) => {
      if (path === "/api/memories") {
        server.deleteAll += 1;
        const deleted = server.memories.length;
        server.memories = [];
        return { data: { deleted }, response: { status: 200, ok: true } };
      }
      server.deleteOne += 1;
      const id = options?.params.path.memory_id;
      server.memories = server.memories.filter((memory) => memory.id !== id);
      return { data: undefined, response: { status: 204, ok: true } };
    },
  );
  return server;
}

function renderList() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <MemoryList />
    </QueryClientProvider>,
  );
}

async function findItem(text: string) {
  const content = await screen.findByText(text);
  const item = content.closest("li");
  if (!item) throw new Error("기억 항목을 찾지 못했습니다");
  return item;
}

const neverResolves = () => new Promise<never>(() => {});

describe("기억 목록", () => {
  beforeEach(() => {
    getMock.mockReset();
    deleteMock.mockReset();
  });

  it("기억 내용과 저장한 날짜, 개수/한도, 삭제 안내를 보여 준다", async () => {
    serve([SPICY, COFFEE]);
    renderList();

    const item = await findItem("매운 음식을 못 먹는다");
    expect(item).toHaveTextContent("저장한 날짜 2026년 11월 17일");
    expect(screen.getByText("아침에 커피를 마신다")).toBeInTheDocument();
    expect(screen.getByText("저장된 기억 2개 / 최대 200개")).toBeInTheDocument();
    expect(
      screen.getByText(/지운 기억은 다음 대화부터 에이전트가 쓰지 않습니다/),
    ).toBeInTheDocument();
  });

  it("기억이 없으면 어떻게 생기는지와 저장하지 않는 경우를 안내한다", async () => {
    serve([]);
    renderList();

    expect(await screen.findByText("아직 저장된 기억이 없습니다.")).toBeInTheDocument();
    const main = document.body;
    expect(main).toHaveTextContent("'나 매운 거 못 먹어, 기억해'처럼 말하면 저장됩니다");
    expect(main).toHaveTextContent("검색·날씨·장소·일정 조회를 쓴 대화에서는 저장되지 않습니다");
    expect(main).toHaveTextContent("비밀번호·카드 번호는 저장하지 않습니다");
    expect(main).toHaveTextContent("저장된 기억 0개 / 최대 200개");
    expect(screen.queryByRole("button", { name: "전체 삭제" })).not.toBeInTheDocument();
  });

  it("'원래 메시지 보기'를 누르면 그 기억을 만든 내 메시지와 시각, 대화 링크를 펼친다", async () => {
    serve([SPICY]);
    renderList();
    const item = await findItem("매운 음식을 못 먹는다");
    const toggle = within(item).getByRole("button", { name: "원래 메시지 보기" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(within(item).queryByText("나 매운 거 못 먹어, 기억해")).not.toBeInTheDocument();

    fireEvent.click(toggle);

    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(within(item).getByText("나 매운 거 못 먹어, 기억해")).toBeInTheDocument();
    expect(item).toHaveTextContent("2026년 11월 17일");
    const link = within(item).getByRole("link", { name: "이 대화로 가기" });
    expect(link).toHaveAttribute("href", "/chat/7");
    fireEvent.click(within(item).getByRole("button", { name: "원래 메시지 접기" }));
    expect(within(item).queryByText("나 매운 거 못 먹어, 기억해")).not.toBeInTheDocument();
  });

  it("기억과 원래 메시지 안의 HTML·태그 모양은 글자 그대로 보인다", async () => {
    const tricky = makeMemory(
      3,
      "<b>굵게</b> <img src=x onerror=alert(1)>",
      "<script>x</script>\n둘째 줄",
    );
    serve([tricky]);
    renderList();
    const item = await findItem("<b>굵게</b> <img src=x onerror=alert(1)>");
    fireEvent.click(within(item).getByRole("button", { name: "원래 메시지 보기" }));

    expect(item.querySelector("b, img, script")).toBeNull();
    expect(item).toHaveTextContent("<script>x</script>");
  });

  it("한 건 삭제: 누르면 그 기억만 지우고 목록을 다시 가져온다. 연타해도 한 번만 보낸다", async () => {
    const server = serve([SPICY, COFFEE]);
    renderList();
    const item = await findItem("매운 음식을 못 먹는다");
    const button = within(item).getByRole("button", { name: "삭제" });

    fireEvent.click(button);
    fireEvent.click(button);

    await waitFor(() =>
      expect(screen.queryByText("매운 음식을 못 먹는다")).not.toBeInTheDocument(),
    );
    expect(server.deleteOne).toBe(1);
    expect(deleteMock).toHaveBeenCalledWith("/api/memories/{memory_id}", {
      params: { path: { memory_id: 1 } },
    });
    expect(screen.getByText("아침에 커피를 마신다")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("기억을 지웠습니다");
  });

  it("한 건 삭제 중에는 버튼을 막고 '지우는 중'을 보여 준다", async () => {
    serve([SPICY]);
    deleteMock.mockImplementation(neverResolves);
    renderList();
    const item = await findItem("매운 음식을 못 먹는다");
    fireEvent.click(within(item).getByRole("button", { name: "삭제" }));

    expect(within(item).getByRole("button", { name: "지우는 중…" })).toBeDisabled();
  });
});

describe("전체 삭제(확인 한 번)", () => {
  beforeEach(() => {
    getMock.mockReset();
    deleteMock.mockReset();
  });

  it("전체 삭제를 누르면 바로 지우지 않고 화면 안에서 묻는다. 취소하면 돌아간다", async () => {
    const server = serve([SPICY, COFFEE]);
    renderList();
    fireEvent.click(await screen.findByRole("button", { name: "전체 삭제" }));

    expect(screen.getByText("기억 2개를 모두 지울까요?")).toBeInTheDocument();
    expect(server.deleteAll).toBe(0);
    fireEvent.click(screen.getByRole("button", { name: "취소" }));

    expect(screen.queryByText("기억 2개를 모두 지울까요?")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "전체 삭제" })).toBeEnabled();
    expect(server.deleteAll).toBe(0);
  });

  it("확인에서 '모두 지우기'를 연타해도 한 번만 보내고, 끝나면 빈 상태 안내가 보인다", async () => {
    const server = serve([SPICY, COFFEE]);
    renderList();
    fireEvent.click(await screen.findByRole("button", { name: "전체 삭제" }));
    const confirm = screen.getByRole("button", { name: "모두 지우기" });

    fireEvent.click(confirm);
    fireEvent.click(confirm);
    fireEvent.click(confirm);

    expect(await screen.findByText("아직 저장된 기억이 없습니다.")).toBeInTheDocument();
    expect(server.deleteAll).toBe(1);
    expect(deleteMock).toHaveBeenCalledWith("/api/memories");
    expect(screen.getByRole("status")).toHaveTextContent("기억 2개를 모두 지웠습니다");
  });

  it("지우는 중에는 확인·취소 버튼과 한 건 삭제 버튼을 막는다", async () => {
    serve([SPICY]);
    renderList();
    fireEvent.click(await screen.findByRole("button", { name: "전체 삭제" }));
    deleteMock.mockImplementation(neverResolves);
    fireEvent.click(screen.getByRole("button", { name: "모두 지우기" }));

    expect(screen.getByRole("button", { name: "모두 지우기" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "취소" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "삭제" })).toBeDisabled();
  });
});

describe("오류", () => {
  beforeEach(() => {
    getMock.mockReset();
    deleteMock.mockReset();
  });

  it("목록을 가져오지 못하면 한국어로 알리고 '다시 불러오기'로 다시 가져온다", async () => {
    getMock.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    renderList();

    expect(await screen.findByRole("alert")).toHaveTextContent("서버에 연결하지 못했습니다");
    serve([SPICY]);
    fireEvent.click(screen.getByRole("button", { name: "다시 불러오기" }));

    expect(await screen.findByText("매운 음식을 못 먹는다")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("서버가 오류로 답하면 상태 코드와 함께 알린다", async () => {
    getMock.mockResolvedValueOnce({ data: undefined, response: { status: 500, ok: false } });
    renderList();
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "기억 목록을 불러오지 못했습니다 (오류 500)",
    );
  });

  it("이미 지워진 기억(404)이면 그렇게 알리고 목록을 다시 가져온다", async () => {
    serve([SPICY, COFFEE]);
    renderList();
    const item = await findItem("매운 음식을 못 먹는다");
    const calls = getMock.mock.calls.length;
    deleteMock.mockResolvedValueOnce({ data: undefined, response: { status: 404, ok: false } });

    fireEvent.click(within(item).getByRole("button", { name: "삭제" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "이미 지워졌거나 찾을 수 없는 기억입니다",
    );
    await waitFor(() => expect(getMock.mock.calls.length).toBeGreaterThan(calls));
    expect(within(item).getByRole("button", { name: "삭제" })).toBeEnabled();
  });

  it("삭제 중 네트워크가 끊기면 한국어로 알리고 다시 누를 수 있다", async () => {
    serve([SPICY]);
    renderList();
    const item = await findItem("매운 음식을 못 먹는다");
    deleteMock.mockRejectedValueOnce(new TypeError("Failed to fetch"));

    fireEvent.click(within(item).getByRole("button", { name: "삭제" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("서버에 연결하지 못했습니다");
    expect(within(item).getByRole("button", { name: "삭제" })).toBeEnabled();
  });

  it("전체 삭제가 실패하면 알리고 확인 영역에서 다시 누를 수 있다", async () => {
    serve([SPICY]);
    renderList();
    fireEvent.click(await screen.findByRole("button", { name: "전체 삭제" }));
    deleteMock.mockResolvedValueOnce({ data: undefined, response: { status: 500, ok: false } });

    fireEvent.click(screen.getByRole("button", { name: "모두 지우기" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("기억을 모두 지우지 못했습니다");
    expect(screen.getByRole("button", { name: "모두 지우기" })).toBeEnabled();
  });
});
