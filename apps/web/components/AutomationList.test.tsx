import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Schedule, Watch } from "@/lib/automations";
import { AutomationList } from "./AutomationList";

// 서버 호출(api.GET / PATCH / DELETE)을 가짜로 바꾼다. 가짜 서버는 예약·감시 목록을 들고 있다.
const getMock = vi.fn();
const patchMock = vi.fn();
const deleteMock = vi.fn();
vi.mock("@/lib/api/client", () => ({
  api: {
    GET: (...args: unknown[]) => getMock(...args),
    PATCH: (...args: unknown[]) => patchMock(...args),
    DELETE: (...args: unknown[]) => deleteMock(...args),
  },
}));

const BRIEFING: Schedule = {
  id: 1,
  title: "아침 브리핑",
  prompt_text: "오늘 일정, 날씨, 미완료 할 일을 요약해라.",
  time_of_day: "07:30",
  weekdays: [0, 1, 2, 3, 4, 5, 6],
  repeat_text: "매일 07:30",
  is_active: true,
  next_run_at: "2026-11-17T22:30:00Z",
  last_run_at: "2026-11-16T22:30:00Z",
  is_briefing: true,
  last_task: {
    id: 501,
    conversation_id: 77,
    status: "done",
    final_answer: "오늘 일정은 2개입니다.\n[제안] 우산 챙기라고 할 일에 추가해줘",
    error: null,
    updated_at: "2026-11-16T22:31:00Z",
  },
};

const WEEKLY: Schedule = {
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

const RAIN: Watch = {
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
    summary: "강수 확률 30%로 조건에 맞지 않습니다. <b>굵게</b>",
    error: null,
    sources: ["https://open-meteo.com/", "javascript:alert(1)"],
  },
};

type FakeServer = { schedules: Schedule[]; watches: Watch[]; patches: number; deletes: number };

function ok(data: unknown, status = 200) {
  return { data, error: undefined, response: { status, ok: true } };
}

function fail(status: number, code: string) {
  return {
    data: undefined,
    error: { detail: { code, message: "서버 문구" } },
    response: { status, ok: false },
  };
}

type Options = { params: { path: Record<string, number> }; body?: { is_active: boolean } };

function serve(schedules: Schedule[], watches: Watch[]): FakeServer {
  const server: FakeServer = {
    schedules: [...schedules],
    watches: [...watches],
    patches: 0,
    deletes: 0,
  };
  getMock.mockImplementation(async (path: string) =>
    path === "/api/schedules"
      ? ok({ schedules: server.schedules, max_active: 10 })
      : ok({ watches: server.watches, max_active: 10 }),
  );
  patchMock.mockImplementation(async (path: string, options: Options) => {
    server.patches += 1;
    const active = options.body?.is_active ?? false;
    if (path.startsWith("/api/schedules")) {
      const id = options.params.path.schedule_id;
      server.schedules = server.schedules.map((s) =>
        s.id === id ? { ...s, is_active: active } : s,
      );
      return ok(server.schedules.find((s) => s.id === id));
    }
    const id = options.params.path.watch_id;
    server.watches = server.watches.map((w) => (w.id === id ? { ...w, is_active: active } : w));
    return ok(server.watches.find((w) => w.id === id));
  });
  deleteMock.mockImplementation(async (path: string, options: Options) => {
    server.deletes += 1;
    if (path.startsWith("/api/schedules")) {
      server.schedules = server.schedules.filter((s) => s.id !== options.params.path.schedule_id);
    } else {
      server.watches = server.watches.filter((w) => w.id !== options.params.path.watch_id);
    }
    return { data: undefined, error: undefined, response: { status: 204, ok: true } };
  });
  return server;
}

function renderList() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <AutomationList />
    </QueryClientProvider>,
  );
}

async function findItem(title: string) {
  const heading = await screen.findByRole("heading", { name: title });
  const item = heading.closest("li");
  if (!item) throw new Error("항목을 찾지 못했습니다");
  return item;
}

beforeEach(() => {
  getMock.mockReset();
  patchMock.mockReset();
  deleteMock.mockReset();
});

describe("자동화 목록", () => {
  it("예약 작업: 반복, 다음 실행, 아침 브리핑 표시, 마지막 실행 결과와 대화 링크", async () => {
    serve([BRIEFING, WEEKLY], []);
    renderList();
    const item = await findItem("아침 브리핑");

    expect(item).toHaveTextContent("매일 07:30");
    expect(item).toHaveTextContent("아침 브리핑");
    expect(item).toHaveTextContent("다음 실행 11월 18일(수) 07:30");
    expect(item).toHaveTextContent("완료");
    expect(item).toHaveTextContent("오늘 일정은 2개입니다.");
    const link = within(item).getByRole("link", { name: "결과 보기" });
    expect(link).toHaveAttribute("href", "/chat/77");
    expect(within(item).getByRole("switch", { name: "켜기·끄기" })).toHaveAttribute(
      "aria-checked",
      "true",
    );

    const weekly = await findItem("다음 주 일정 요약");
    expect(weekly).toHaveTextContent("매주 일요일 21:00");
    expect(weekly).toHaveTextContent("꺼져 있어 실행하지 않습니다");
    expect(weekly).toHaveTextContent("아직 실행한 적이 없습니다");
    expect(screen.getByText("켜진 예약 작업 1개 / 최대 10개")).toBeInTheDocument();
  });

  it("조건 감시: 조건, 간격, 만료일, 마지막 확인 결과(요약은 글자로만, 출처는 http·https만 링크)", async () => {
    serve([], [RAIN]);
    renderList();
    const item = await findItem("내일 서울 비 확률");

    expect(item).toHaveTextContent("강수 확률이 60%를 넘으면");
    expect(item).toHaveTextContent("1시간마다 확인");
    expect(item).toHaveTextContent("한 번 알리면 꺼집니다");
    expect(item).toHaveTextContent("만료 12월 17일");
    expect(item).toHaveTextContent("조건에 맞지 않음");
    expect(item).toHaveTextContent("<b>굵게</b>");
    expect(item.querySelector("b")).toBeNull();
    const link = within(item).getByRole("link", { name: "https://open-meteo.com/" });
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    expect(within(item).queryByRole("link", { name: "javascript:alert(1)" })).toBeNull();
    expect(item).toHaveTextContent("javascript:alert(1)");
    expect(screen.getByText("켜진 조건 감시 1개 / 최대 10개")).toBeInTheDocument();
  });

  it("빈 상태: 채팅에서 만드는 예시 문장과 조회만 한다는 안내", async () => {
    serve([], []);
    renderList();

    expect(
      await screen.findByText(/매주 일요일 밤 9시에 다음 주 일정 요약해줘/),
    ).toBeInTheDocument();
    expect(screen.getByText(/내일 서울에 비 올 확률이 60% 넘으면 알려줘/)).toBeInTheDocument();
    expect(document.body).toHaveTextContent("자동 작업은 조회만 합니다");
    expect(document.body).toHaveTextContent("[제안]");
  });
});

describe("켜기·끄기", () => {
  it("스위치를 누르면 PATCH 한 번(연타해도), 끝나면 상태가 바뀐다", async () => {
    const server = serve([BRIEFING], []);
    renderList();
    const item = await findItem("아침 브리핑");
    const toggle = within(item).getByRole("switch", { name: "켜기·끄기" });

    fireEvent.click(toggle);
    fireEvent.click(toggle);

    await waitFor(() => expect(toggle).toHaveAttribute("aria-checked", "false"));
    expect(server.patches).toBe(1);
    expect(patchMock).toHaveBeenCalledWith("/api/schedules/{schedule_id}", {
      params: { path: { schedule_id: 1 } },
      body: { is_active: false },
    });
  });

  it("바꾸는 동안 스위치와 삭제를 막는다", async () => {
    serve([BRIEFING], []);
    patchMock.mockImplementation(() => new Promise<never>(() => {}));
    renderList();
    const item = await findItem("아침 브리핑");
    fireEvent.click(within(item).getByRole("switch", { name: "켜기·끄기" }));

    expect(within(item).getByRole("switch", { name: "켜기·끄기" })).toBeDisabled();
    expect(within(item).getByRole("button", { name: "삭제" })).toBeDisabled();
  });

  it("409 limit_reached·expired는 코드별 한국어로 알린다", async () => {
    serve([WEEKLY], [{ ...RAIN, is_active: false }]);
    renderList();
    const weekly = await findItem("다음 주 일정 요약");
    patchMock.mockResolvedValueOnce(fail(409, "limit_reached"));
    fireEvent.click(within(weekly).getByRole("switch", { name: "켜기·끄기" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "켜 둘 수 있는 예약 작업은 최대 10개입니다",
    );

    const rain = await findItem("내일 서울 비 확률");
    patchMock.mockResolvedValueOnce(fail(409, "expired"));
    fireEvent.click(within(rain).getByRole("switch", { name: "켜기·끄기" }));
    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent("만료된 감시는 다시 켤 수 없습니다"),
    );
  });

  it("네트워크가 끊기면 한국어로 알린다", async () => {
    serve([BRIEFING], []);
    renderList();
    const item = await findItem("아침 브리핑");
    patchMock.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    fireEvent.click(within(item).getByRole("switch", { name: "켜기·끄기" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("서버에 연결하지 못했습니다");
  });
});

describe("삭제(화면 안 확인 한 번)", () => {
  it("삭제를 누르면 바로 지우지 않고 묻는다. 취소하면 돌아간다", async () => {
    const server = serve([BRIEFING], []);
    renderList();
    const item = await findItem("아침 브리핑");
    fireEvent.click(within(item).getByRole("button", { name: "삭제" }));

    expect(within(item).getByText("이 예약 작업을 지울까요?")).toBeInTheDocument();
    expect(server.deletes).toBe(0);
    fireEvent.click(within(item).getByRole("button", { name: "취소" }));
    expect(within(item).queryByText("이 예약 작업을 지울까요?")).not.toBeInTheDocument();
    expect(server.deletes).toBe(0);
  });

  it("확인의 '지우기'를 연타해도 한 번만 보내고, 목록에서 사라진다", async () => {
    const server = serve([], [RAIN]);
    renderList();
    const item = await findItem("내일 서울 비 확률");
    fireEvent.click(within(item).getByRole("button", { name: "삭제" }));
    expect(within(item).getByText("이 조건 감시를 지울까요?")).toBeInTheDocument();
    const confirm = within(item).getByRole("button", { name: "지우기" });

    fireEvent.click(confirm);
    fireEvent.click(confirm);

    await waitFor(() =>
      expect(screen.queryByRole("heading", { name: "내일 서울 비 확률" })).not.toBeInTheDocument(),
    );
    expect(server.deletes).toBe(1);
    expect(deleteMock).toHaveBeenCalledWith("/api/watches/{watch_id}", {
      params: { path: { watch_id: 9 } },
    });
    expect(screen.getByRole("status")).toHaveTextContent("조건 감시를 지웠습니다");
  });

  it("이미 지워진 것(404)이면 그렇게 알린다", async () => {
    serve([BRIEFING], []);
    renderList();
    const item = await findItem("아침 브리핑");
    deleteMock.mockResolvedValueOnce(fail(404, "not_found"));
    fireEvent.click(within(item).getByRole("button", { name: "삭제" }));
    fireEvent.click(within(item).getByRole("button", { name: "지우기" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "이미 지워졌거나 찾을 수 없는 예약 작업입니다",
    );
  });
});

describe("불러오기 오류", () => {
  it("목록을 가져오지 못하면 알리고 다시 불러올 수 있다", async () => {
    getMock.mockResolvedValue({ data: undefined, error: {}, response: { status: 500, ok: false } });
    renderList();
    const alerts = await screen.findAllByRole("alert");
    expect(alerts[0]).toHaveTextContent("예약 작업 목록을 불러오지 못했습니다 (오류 500)");

    serve([BRIEFING], []);
    const [reload] = screen.getAllByRole("button", { name: "다시 불러오기" });
    if (!reload) throw new Error("다시 불러오기 버튼이 없습니다");
    fireEvent.click(reload);
    expect(await screen.findByRole("heading", { name: "아침 브리핑" })).toBeInTheDocument();
  });
});
