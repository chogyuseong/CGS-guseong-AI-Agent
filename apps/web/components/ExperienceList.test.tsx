import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ExperienceList as ListData } from "@/lib/experiences";
import { ExperienceList } from "./ExperienceList";

// 설정 화면 '내 경험'(생활 비서 D, 2026-11-18 사용자 결정: 원문 암호화 + 등록 전 안내).
// 서버 호출(api.GET/POST/DELETE)을 가짜로 바꾼다.
const getMock = vi.fn();
const postMock = vi.fn();
const deleteMock = vi.fn();
vi.mock("@/lib/api/client", () => ({
  api: {
    GET: (...args: unknown[]) => getMock(...args),
    POST: (...args: unknown[]) => postMock(...args),
    DELETE: (...args: unknown[]) => deleteMock(...args),
  },
}));

function listData(overrides: Partial<ListData> = {}): ListData {
  return { experiences: [], limit: 10, max_body_chars: 2000, enabled: true, ...overrides };
}

function serveList(data: ListData) {
  getMock.mockImplementation(async (path: string) =>
    path === "/api/experiences"
      ? { data, response: { status: 200, ok: true } }
      : {
          data: { ...data.experiences[0], body: "원문 본문" },
          response: { status: 200, ok: true },
        },
  );
}

function renderList() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <ExperienceList />
    </QueryClientProvider>,
  );
}

function fill(title: string, body: string) {
  fireEvent.change(screen.getByLabelText("제목"), { target: { value: title } });
  fireEvent.change(screen.getByLabelText(/본문/), { target: { value: body } });
}

describe("ExperienceList", () => {
  beforeEach(() => {
    getMock.mockReset();
    postMock.mockReset();
    deleteMock.mockReset();
  });

  it("안내를 읽었다고 체크해야 저장할 수 있고, 저장하면 서버로 보낸다", async () => {
    serveList(listData());
    postMock.mockResolvedValue({
      data: { id: 1, title: "프로젝트", length: 4, created_at: "2026-11-18T00:00:00Z" },
      response: { status: 201, ok: true },
    });
    renderList();

    await screen.findByText("등록 전에 확인해 주세요");
    expect(screen.getByText(/암호화해서 저장합니다/)).toBeInTheDocument();
    expect(screen.getByText(/전화번호·이메일·주소는 빼고/)).toBeInTheDocument();
    fill("프로젝트", "본문 내용");
    const save = screen.getByRole("button", { name: "경험 저장" });
    expect(save).toBeDisabled();

    fireEvent.click(screen.getByLabelText("안내를 읽었습니다"));
    fireEvent.click(save);

    await waitFor(() =>
      expect(postMock).toHaveBeenCalledWith("/api/experiences", {
        body: { title: "프로젝트", body: "본문 내용" },
      }),
    );
    await waitFor(() => expect(screen.getByLabelText("안내를 읽었습니다")).not.toBeChecked());
  });

  it("서버가 거절한 이유(연락처 등)를 그대로 보여 준다", async () => {
    serveList(listData());
    postMock.mockResolvedValue({
      data: undefined,
      error: { detail: "전화번호로 보이는 내용이 있어 저장하지 않았습니다." },
      response: { status: 400, ok: false },
    });
    renderList();
    await screen.findByText("등록 전에 확인해 주세요");

    fill("프로젝트", "본문");
    fireEvent.click(screen.getByLabelText("안내를 읽었습니다"));
    fireEvent.click(screen.getByRole("button", { name: "경험 저장" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("전화번호로 보이는 내용");
  });

  it("목록에서 본문을 열어 보고 지울 수 있다", async () => {
    serveList(
      listData({
        experiences: [
          { id: 3, title: "마이에이전트", length: 5, created_at: "2026-11-18T00:00:00Z" },
        ],
      }),
    );
    deleteMock.mockResolvedValue({ response: { status: 204, ok: true } });
    renderList();

    expect(await screen.findByText("마이에이전트")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "보기" }));
    expect(await screen.findByText("원문 본문")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "마이에이전트 지우기" }));

    await waitFor(() =>
      expect(deleteMock).toHaveBeenCalledWith("/api/experiences/{experience_id}", {
        params: { path: { experience_id: 3 } },
      }),
    );
  });

  it("암호화 키가 없으면 등록 칸 대신 안내만 보인다", async () => {
    serveList(listData({ enabled: false }));
    renderList();

    expect(await screen.findByText(/암호화 키\(PAYLOAD_ENCRYPTION_KEY\)/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "경험 저장" })).not.toBeInTheDocument();
  });

  it("한도에 닿으면 등록 칸 대신 안내가 보인다", async () => {
    serveList(
      listData({
        limit: 1,
        experiences: [{ id: 3, title: "하나", length: 5, created_at: "2026-11-18T00:00:00Z" }],
      }),
    );
    renderList();

    expect(await screen.findByText(/1개까지 저장할 수 있습니다/)).toBeInTheDocument();
  });
});
