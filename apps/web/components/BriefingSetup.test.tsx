import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Schedule } from "@/lib/automations";
import { BriefingSetup } from "./BriefingSetup";

const putMock = vi.fn();
vi.mock("@/lib/api/client", () => ({
  api: { PUT: (...args: unknown[]) => putMock(...args) },
}));

const ACTIVE: Schedule = {
  id: 3,
  title: "아침 브리핑",
  prompt_text: "브리핑",
  time_of_day: "08:10",
  weekdays: [0, 1, 2, 3, 4, 5, 6],
  repeat_text: "매일 08:10",
  is_active: true,
  next_run_at: "2026-11-17T23:10:00Z",
  last_run_at: null,
  is_briefing: true,
  last_task: null,
};

function renderSetup(briefing: Schedule | undefined) {
  const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <BriefingSetup briefing={briefing} maxActive={10} />
    </QueryClientProvider>,
  );
}

beforeEach(() => putMock.mockReset());

describe("아침 브리핑 켜기(6단계 보강)", () => {
  it("켜기 전에는 실행하지 않는다고 알리고, 기본 07:30으로 켠다", async () => {
    putMock.mockResolvedValue({
      data: ACTIVE,
      error: undefined,
      response: { status: 200, ok: true },
    });
    renderSetup(undefined);

    expect(screen.getByText(/켜기 전에는 실행하지 않습니다/)).toBeInTheDocument();
    expect(screen.getByText(/매일 한 번 모델 비용이 듭니다/)).toBeInTheDocument();
    expect(screen.getByLabelText("브리핑 시각")).toHaveValue("07:30");
    fireEvent.click(screen.getByRole("button", { name: "브리핑 켜기" }));

    await waitFor(() => expect(putMock).toHaveBeenCalledTimes(1));
    expect(putMock.mock.calls[0]?.[0]).toBe("/api/schedules/briefing");
    expect(putMock.mock.calls[0]?.[1]).toEqual({ body: { time_of_day: "07:30" } });
  });

  it("켜져 있으면 지금 시각을 보여 주고 시각을 바꿀 수 있다", async () => {
    putMock.mockResolvedValue({
      data: ACTIVE,
      error: undefined,
      response: { status: 200, ok: true },
    });
    renderSetup(ACTIVE);

    expect(screen.getByText("매일 08:10에 실행 중")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("브리핑 시각"), { target: { value: "06:45" } });
    fireEvent.click(screen.getByRole("button", { name: "시각 바꾸기" }));

    await waitFor(() => expect(putMock).toHaveBeenCalledTimes(1));
    expect(putMock.mock.calls[0]?.[1]).toEqual({ body: { time_of_day: "06:45" } });
  });

  it("켤 수 있는 개수가 다 찼으면 이유를 보여 준다", async () => {
    putMock.mockResolvedValue({
      data: undefined,
      error: { detail: { code: "limit_reached", message: "x" } },
      response: { status: 409, ok: false },
    });
    renderSetup(undefined);

    fireEvent.click(screen.getByRole("button", { name: "브리핑 켜기" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("최대 10개");
  });
});
