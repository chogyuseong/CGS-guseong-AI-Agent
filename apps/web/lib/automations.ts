// 자동화 화면(6단계)이 쓰는 서버 호출과 문구. 타입은 pnpm gen:api가 만든 schema.d.ts에서 꺼낸다.
//
// API(apps/api/app/routers/automations.py):
//   GET    /api/schedules              → {schedules: 시각 순, max_active: 10}
//   PATCH  /api/schedules/{id}         → 본문 {is_active}. 404 not_found / 409 limit_reached
//   DELETE /api/schedules/{id}         → 204 / 404
//   GET    /api/watches                → {watches: 만든 순, max_active: 10}
//   PATCH  /api/watches/{id}           → 본문 {is_active}. 404 / 409 limit_reached·duplicate·expired
//   DELETE /api/watches/{id}           → 204 / 404
// 오류 본문은 {detail: {code, message}}. 화면은 code로 고정 문구를 고른다(서버 문장은 옮기지 않는다).
// 만들거나 내용을 고치는 API는 없다. 예약과 감시는 채팅에서 말로 만든다.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";

type Schemas = components["schemas"];
export type Schedule = Schemas["ScheduleOut"];
export type Watch = Schemas["WatchOut"];
export type WatchStatus = Schemas["WatchStatus"];
export type AutomationKind = "schedule" | "watch";

export const automationKeys = {
  schedules: ["schedules"] as const,
  watches: ["watches"] as const,
};

// 서버의 '예약 작업' 대화방 제목(apps/api/app/automation/rules.py의 AUTOMATION_CONVERSATION_TITLE).
// 예약 결과와 감시 알림이 이 대화방에 남는다. 서버도 이 제목으로 대화방을 찾는다.
export const AUTOMATION_CONVERSATION_TITLE = "예약 작업";

export function isAutomationRoom(title: string | undefined): boolean {
  return title === AUTOMATION_CONVERSATION_TITLE;
}

const NETWORK_ERROR = "서버에 연결하지 못했습니다. 네트워크를 확인해 주세요.";

const NOUN: Record<AutomationKind, string> = { schedule: "예약 작업", watch: "조건 감시" };

type ErrorInput = {
  kind: AutomationKind;
  status: number;
  code: string | null;
  maxActive: number;
};

// 켜기·끄기·삭제 실패를 한국어 문구로. 409는 코드마다 다르게 안내한다.
export function automationErrorText({ kind, status, code, maxActive }: ErrorInput): string {
  if (status === 404) {
    return `이미 지워졌거나 찾을 수 없는 ${NOUN[kind]}입니다. 목록을 다시 불러왔습니다.`;
  }
  if (status === 409 && code === "limit_reached") {
    return kind === "schedule"
      ? `켜 둘 수 있는 예약 작업은 최대 ${maxActive}개입니다. 다른 예약 작업을 먼저 꺼 주세요.`
      : `켜 둘 수 있는 조건 감시는 최대 ${maxActive}개입니다. 다른 감시를 먼저 꺼 주세요.`;
  }
  if (status === 409 && code === "duplicate") {
    return kind === "schedule"
      ? "같은 시각·요일·요청의 예약 작업이 이미 있습니다. 그 예약을 쓰거나 먼저 지워 주세요."
      : "같은 조회·조건으로 켜진 감시가 이미 있습니다. 그 감시를 쓰거나 먼저 꺼 주세요.";
  }
  if (status === 409 && code === "expired") {
    return "만료된 감시는 다시 켤 수 없습니다. 채팅에서 새로 만들어 주세요.";
  }
  return `요청을 처리하지 못했습니다 (오류 ${status}). 잠시 후 다시 시도해 주세요.`;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

// 오류 본문에서 code만 꺼낸다. 모양이 다르면 null.
export function errorCode(body: unknown): string | null {
  if (!isRecord(body) || !isRecord(body.detail)) return null;
  return typeof body.detail.code === "string" ? body.detail.code : null;
}

const WEEKDAYS = "일월화수목금토";
const SEOUL_PARTS = new Intl.DateTimeFormat("en-CA", {
  timeZone: "Asia/Seoul",
  year: "numeric",
  month: "numeric",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
});

// 서버 시각(UTC ISO)을 서울 기준 "11월 18일(수) 07:30"으로. 시각이 아니면 원래 글자.
export function formatRunTime(value: string): string {
  const instant = new Date(value);
  if (Number.isNaN(instant.getTime())) return value;
  const parts = Object.fromEntries(
    SEOUL_PARTS.formatToParts(instant).map((part) => [part.type, part.value]),
  );
  const date = Date.UTC(Number(parts.year), Number(parts.month) - 1, Number(parts.day));
  const weekday = new Date(date).getUTCDay();
  return `${parts.month}월 ${parts.day}일(${WEEKDAYS[weekday]}) ${parts.hour}:${parts.minute}`;
}

export function intervalText(minutes: number): string {
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  if (hours === 0) return `${rest}분마다`;
  return rest === 0 ? `${hours}시간마다` : `${hours}시간 ${rest}분마다`;
}

export function activeCountText(noun: string, count: number, max: number): string {
  return `켜진 ${noun} ${count}개 / 최대 ${max}개`;
}

// 긴 답변의 앞부분만(줄바꿈은 공백 하나로). 전체는 대화방에서 본다.
export function shortText(value: string, max: number): string {
  const flat = value.replace(/\s+/g, " ").trim();
  return flat.length > max ? `${flat.slice(0, max)}…` : flat;
}

export const TASK_STATUS_TEXT: Record<Schemas["TaskStatus"], string> = {
  queued: "곧 실행",
  running: "실행 중",
  waiting_input: "되물음",
  waiting_approval: "승인 대기",
  cancel_requested: "취소 중",
  reconciling: "결과 확인 중",
  done: "완료",
  partially_done: "일부 완료",
  failed: "실패",
  cancelled: "취소됨",
  budget_exceeded: "비용 한도로 중단",
  needs_review: "확인 필요",
};

// 6단계 보강: unknown은 '조건 아님'이 아니다(조회·판정을 못 해 판단 보류). tool_failed·judge_failed·skipped_budget은 보강 전 기록.
export const WATCH_STATUS_TEXT: Record<WatchStatus, string> = {
  met: "조건 충족",
  not_met: "조건에 맞지 않음",
  unknown: "판단 보류",
  tool_failed: "판단 보류 · 조회 실패",
  judge_failed: "판단 보류 · 판정 실패",
  skipped_budget: "하루 비용 한도로 쉬는 중",
  paused_budget: "하루 비용 한도로 쉬는 중",
  expired: "만료되어 꺼짐",
};

const UNKNOWN_REASON_TEXT: Record<string, string> = {
  tool_failed: "조회 실패",
  judge_failed: "판정 실패",
  value_missing: "결과에 비교할 값이 없음",
};

// 마지막 확인 결과 한 줄. unknown이면 이유를 붙인다.
export function watchStatusText(status: WatchStatus, reason: string | null | undefined): string {
  const base = WATCH_STATUS_TEXT[status];
  const why = status === "unknown" && reason ? UNKNOWN_REASON_TEXT[reason] : undefined;
  return why ? `${base} · ${why}` : base;
}

// 네트워크가 끊기면 fetch가 영어 오류를 던지므로 한국어 문구로 바꾼다.
function networkError(): never {
  throw new Error(NETWORK_ERROR);
}

async function fetchSchedules() {
  const { data, response } = await api.GET("/api/schedules").catch(networkError);
  if (data === undefined)
    throw new Error(`예약 작업 목록을 불러오지 못했습니다 (오류 ${response.status})`);
  return data;
}

async function fetchWatches() {
  const { data, response } = await api.GET("/api/watches").catch(networkError);
  if (data === undefined)
    throw new Error(`조건 감시 목록을 불러오지 못했습니다 (오류 ${response.status})`);
  return data;
}

export function useSchedules() {
  return useQuery({ queryKey: automationKeys.schedules, queryFn: fetchSchedules });
}

export function useWatches() {
  return useQuery({ queryKey: automationKeys.watches, queryFn: fetchWatches });
}

type Result = { error?: unknown; response: { status: number; ok: boolean } };

function throwIfFailed(result: Result, kind: AutomationKind, maxActive: number): void {
  if (result.response.ok) return;
  const status = result.response.status;
  throw new Error(automationErrorText({ kind, status, code: errorCode(result.error), maxActive }));
}

export type ToggleInput = { id: number; active: boolean; maxActive: number };

async function patchActive(kind: AutomationKind, { id, active }: ToggleInput): Promise<Result> {
  const body = { is_active: active };
  const request =
    kind === "schedule"
      ? api.PATCH("/api/schedules/{schedule_id}", { params: { path: { schedule_id: id } }, body })
      : api.PATCH("/api/watches/{watch_id}", { params: { path: { watch_id: id } }, body });
  return request.catch(networkError);
}

async function deleteOne(kind: AutomationKind, id: number): Promise<Result> {
  const request =
    kind === "schedule"
      ? api.DELETE("/api/schedules/{schedule_id}", { params: { path: { schedule_id: id } } })
      : api.DELETE("/api/watches/{watch_id}", { params: { path: { watch_id: id } } });
  return request.catch(networkError);
}

function listKey(kind: AutomationKind) {
  return kind === "schedule" ? automationKeys.schedules : automationKeys.watches;
}

// 켜기·끄기와 삭제는 성공·실패 모두 목록을 다시 가져온다(실패해도 서버 상태가 바뀌었을 수 있다. 404면 이미 없는 것).
export function useSetActive(kind: AutomationKind) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (input: ToggleInput) => {
      throwIfFailed(await patchActive(kind, input), kind, input.maxActive);
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: listKey(kind) }),
  });
}

export function useDeleteAutomation(kind: AutomationKind) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, maxActive }: { id: number; maxActive: number }) => {
      throwIfFailed(await deleteOne(kind, id), kind, maxActive);
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: listKey(kind) }),
  });
}

// 6단계 보강: 아침 브리핑 켜기·시각 바꾸기(PUT /api/schedules/briefing). 끄기·지우기는 다른 예약과 같다.
export function useEnableBriefing(maxActive: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (timeOfDay: string) => {
      const result = await api
        .PUT("/api/schedules/briefing", { body: { time_of_day: timeOfDay } })
        .catch(networkError);
      throwIfFailed(result, "schedule", maxActive);
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: automationKeys.schedules }),
  });
}
