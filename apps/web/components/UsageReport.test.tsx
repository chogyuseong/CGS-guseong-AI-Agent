import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Usage } from "@/lib/usage";
import { UsageReport } from "./UsageReport";

// 서버 호출(api.GET)을 가짜로 바꾼다. 요청한 days에 맞춰 날짜를 채운 응답을 돌려준다.
const getMock = vi.fn();
vi.mock("@/lib/api/client", () => ({
  api: { GET: (...args: unknown[]) => getMock(...args) },
}));

const ZERO = { input_tokens: 0, output_tokens: 0, total_tokens: 0, cost_usd: 0, calls: 0 };
type Totals = typeof ZERO;

function totals(cost: number, input: number, output: number, calls: number): Totals {
  return {
    input_tokens: input,
    output_tokens: output,
    total_tokens: input + output,
    cost_usd: cost,
    calls,
  };
}

function dayList(count: number, used: Record<string, Totals>) {
  return Array.from({ length: count }, (_, index) => {
    const day = new Date(Date.UTC(2026, 10, 17 - (count - 1 - index)));
    const date = day.toISOString().slice(0, 10);
    return { date, ...(used[date] ?? ZERO) };
  });
}

function usage(days: number, options: { empty?: boolean; exceeded?: boolean } = {}): Usage {
  const today = totals(options.exceeded ? 1.2 : 0.0123, 9000, 1000, 3);
  const small = totals(0.000004, 30, 2, 1);
  const used: Record<string, Totals> = options.empty
    ? {}
    : { "2026-11-17": today, "2026-11-15": small };
  const total = options.empty ? ZERO : totals(today.cost_usd + small.cost_usd, 9030, 1002, 4);
  return {
    timezone: "Asia/Seoul",
    days: dayList(days, used),
    by_purpose: [
      { purpose: "agent", ...(options.empty ? ZERO : totals(0.011, 8000, 900, 2)) },
      { purpose: "guard", ...(options.empty ? ZERO : totals(0.0013, 1030, 102, 2)) },
      { purpose: "watch", ...ZERO },
      { purpose: "eval", ...ZERO },
    ],
    by_model: options.empty ? [] : [{ model: "claude-haiku-5-5", ...total }],
    total,
    today: {
      date: "2026-11-17",
      cost_usd: options.empty ? 0 : today.cost_usd,
      limit_usd: 1,
      remaining_usd: options.exceeded ? 0 : options.empty ? 1 : 1 - today.cost_usd,
      exceeded: options.exceeded ?? false,
    },
  };
}

type Query = { params: { query: { days: number } } };

function serve(options: { empty?: boolean; exceeded?: boolean } = {}) {
  getMock.mockImplementation(async (_path: string, init: Query) => ({
    data: usage(init.params.query.days, options),
    response: { status: 200, ok: true },
  }));
}

function renderReport() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <UsageReport />
    </QueryClientProvider>,
  );
}

const requestedDays = () => getMock.mock.calls.map(([, init]) => (init as Query).params.query.days);

describe("비용 화면", () => {
  beforeEach(() => {
    getMock.mockReset();
  });

  it("처음에는 최근 14일을 /api/usage/daily로 요청한다", async () => {
    serve();
    renderReport();
    await screen.findByRole("region", { name: "오늘" });
    expect(getMock).toHaveBeenCalledWith("/api/usage/daily", { params: { query: { days: 14 } } });
  });

  it("오늘 쓴 금액·한도·남은 금액과 진행 막대를 보여 준다", async () => {
    serve();
    renderReport();
    const today = await screen.findByRole("region", { name: "오늘" });
    expect(today).toHaveTextContent("쓴 금액$0.0123");
    expect(today).toHaveTextContent("하루 한도$1.0000");
    expect(today).toHaveTextContent("남은 금액$0.9877");
    const bar = within(today).getByRole("progressbar", { name: "오늘 한도 사용률" });
    expect(bar).toHaveAttribute("aria-valuenow", "1");
    expect(bar).toHaveAttribute("aria-valuetext", "1% 사용");
    expect(screen.queryByText(/오늘 한도를 넘어/)).toBeNull();
  });

  it("한도를 넘었으면 새 작업이 모델을 부르지 않는다고 알린다", async () => {
    serve({ exceeded: true });
    renderReport();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("오늘 한도를 넘어 새 작업이 모델을 부르지 않습니다");
    expect(screen.getByRole("region", { name: "오늘" })).toHaveTextContent("남은 금액$0.0000");
  });

  it("일별 사용량을 표로 읽을 수 있다(최근 날짜부터, 작은 금액도 0으로 보이지 않게)", async () => {
    serve();
    renderReport();
    const table = await screen.findByRole("table", { name: /일별 사용량/ });
    const rows = within(table).getAllByRole("row");
    expect(rows).toHaveLength(15); // 머리줄 + 14일
    expect(rows[1]).toHaveTextContent("11월 17일(화)");
    expect(rows[1]).toHaveTextContent("$0.0123");
    expect(rows[1]).toHaveTextContent("10,000");
    expect(rows[3]).toHaveTextContent("11월 15일(일)");
    expect(rows[3]).toHaveTextContent("$0.0001 미만");
    expect(rows[2]).toHaveTextContent("$0.0000");
  });

  it("용도별 합계는 한국어 이름과 설명, 모델별 합계도 보여 준다", async () => {
    serve();
    renderReport();
    const purposes = await screen.findByRole("region", { name: "용도별 합계" });
    for (const name of ["에이전트", "감시 에이전트(승인 검사)", "조건 감시 판정", "평가"]) {
      expect(within(purposes).getByText(name)).toBeInTheDocument();
    }
    expect(purposes).toHaveTextContent("$0.0110");
    const models = screen.getByRole("region", { name: "모델별 합계" });
    expect(models).toHaveTextContent("claude-haiku-5-5");
    expect(models).toHaveTextContent("호출 4회");
  });

  it("기간을 바꾸면 그 일수로 다시 요청하고 표의 줄 수가 바뀐다", async () => {
    serve();
    renderReport();
    await screen.findByRole("table", { name: /일별 사용량/ });
    const week = screen.getByRole("button", { name: "7일" });
    expect(screen.getByRole("button", { name: "14일" })).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(week);
    await waitFor(() => expect(requestedDays()).toContain(7));
    await waitFor(() =>
      expect(within(screen.getByRole("table")).getAllByRole("row")).toHaveLength(8),
    );
    expect(week).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(screen.getByRole("button", { name: "30일" }));
    await waitFor(() => expect(requestedDays()).toContain(30));
  });

  it("기간 안에 사용이 없으면 빈 상태 안내를 보여 주고 표는 그리지 않는다", async () => {
    serve({ empty: true });
    renderReport();
    expect(await screen.findByText("이 기간에는 모델을 부른 기록이 없습니다.")).toBeInTheDocument();
    expect(screen.queryByRole("table")).toBeNull();
    expect(screen.getByRole("region", { name: "오늘" })).toHaveTextContent("남은 금액$1.0000");
  });

  it("불러오지 못하면 오류와 다시 불러오기 버튼을 보여 준다", async () => {
    getMock.mockResolvedValue({ data: undefined, response: { status: 500, ok: false } });
    renderReport();
    expect(await screen.findByRole("alert")).toHaveTextContent("오류 500");
    expect(screen.getByRole("button", { name: "다시 불러오기" })).toBeInTheDocument();
  });
});
