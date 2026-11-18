// 비용 화면(9단계 2번)이 쓰는 서버 호출과 표기. 타입은 pnpm gen:api가 만든 schema.d.ts에서 꺼낸다.
//
// API(apps/api/app/routers/usage.py):
//   GET /api/usage/daily?days=N(1~90) → 일별(오래된 날부터, 빈 날은 0), 용도별(항상 4개), 모델별, 기간 합계, 오늘 한도
// 날짜 경계는 서버의 하루 비용 상한 검사와 같은 서울 0시다. 금액은 달러 숫자(소수 6자리).
// 한도를 바꾸는 API·화면은 없다(명세에 없음). 한도는 서버 설정(DAILY_COST_LIMIT_USD)이다.
//
// 쓰는 곳: components/UsageReport.tsx(기간 선택, 오늘 카드, 빈 상태), UsageSections.tsx(일별 표·용도별·모델별).
// 근거: DECISIONS 2026-11-17 "화면(비용)" 절(7·14·30일, 기본 14일, 금액 4자리·'미만', 사용률 내림, 초과 여부는 서버 값만,
// 용도 이름, 빈 상태는 total.calls 0), 2026-11-18 9단계 보강(경로를 /api/usage/daily로 옮김, 옛 /api/usage는 404).
//
// 고칠 때 주의:
// - 용도 키(agent·guard·watch·eval)는 서버 비용 기록의 purpose 값과 글자로 맞물린다. 모르는 값은 서버 글자를 이름으로 쓴다.
// - 한도 초과 여부는 화면에서 계산하지 말고 today.exceeded(서버 상한 검사와 같은 값)를 쓴다.
// - 응답 모양이 바뀌면 pnpm gen:api 뒤 여기와 UsageReport·UsageSections를 본다.
// - 테스트: lib/usage.test.ts, components/UsageReport.test.tsx, e2e/usage.spec.ts, e2e/widths.spec.ts(/usage).
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";

type Schemas = components["schemas"];
export type Usage = Schemas["UsageOut"];
export type UsageToday = Schemas["UsageTodayOut"];
export type UsageDay = Schemas["UsageDayOut"];

// 기간 선택지(일). 서버는 1~90일을 받는다.
export const PERIOD_OPTIONS = [7, 14, 30] as const;
export type Period = (typeof PERIOD_OPTIONS)[number];
export const DEFAULT_PERIOD: Period = 14;

// 기간마다 따로 캐시한다(["usage", days]). all은 모든 기간을 한꺼번에 가리키는 앞부분이다(지금 쓰는 곳은 없다).
export const usageKeys = {
  all: ["usage"] as const,
  period: (days: number) => ["usage", days] as const,
};

const NETWORK_ERROR = "서버에 연결하지 못했습니다. 네트워크를 확인해 주세요.";
// 이보다 작은 금액은 소수 4자리로 쓰면 $0.0000이 되어 '안 쓴 것'처럼 보이므로 '미만'으로 쓴다.
const SMALLEST_SHOWN = 0.0001;

const usdFormat = new Intl.NumberFormat("en-US", {
  minimumFractionDigits: 4,
  maximumFractionDigits: 4,
});
const tokenFormat = new Intl.NumberFormat("ko-KR");

// 달러 금액을 "$0.0123"으로. 0 이하·NaN은 "$0.0000", 0보다 크고 $0.0001보다 작으면 "$0.0001 미만".
export function formatUsd(value: number): string {
  if (!Number.isFinite(value) || value <= 0) return "$0.0000";
  if (value < SMALLEST_SHOWN) return "$0.0001 미만";
  return `$${usdFormat.format(value)}`;
}

export function formatTokens(value: number): string {
  return tokenFormat.format(value);
}

// "2026-11-17"(서울 날짜) → "11월 17일(화)". 날짜만 있는 값이라 서울 0시로 읽고 서울 기준으로 쓴다.
export function formatUsageDay(date: string): string {
  const at = new Date(`${date}T00:00:00+09:00`);
  const parts = new Intl.DateTimeFormat("ko-KR", {
    timeZone: "Asia/Seoul",
    month: "numeric",
    day: "numeric",
    weekday: "short",
  }).formatToParts(at);
  const part = (type: Intl.DateTimeFormatPartTypes) =>
    parts.find((item) => item.type === type)?.value ?? "";
  return `${part("month")}월 ${part("day")}일(${part("weekday")})`;
}

// 오늘 쓴 금액 / 한도(0~1). 한도가 0이면 조금이라도 썼을 때 가득 찬 것으로 본다.
export function todayRatio(today: UsageToday): number {
  if (today.limit_usd <= 0) return today.cost_usd > 0 ? 1 : 0;
  return Math.min(Math.max(today.cost_usd / today.limit_usd, 0), 1);
}

// 진행 막대 옆 글자. 내림이라 한도를 넘기 전에는 100%가 되지 않고, 조금이라도 썼으면 0% 대신 '1% 미만'.
export function todayPercentText(today: UsageToday): string {
  const percent = todayRatio(today) * 100;
  if (percent > 0 && percent < 1) return "1% 미만";
  return `${Math.floor(percent)}%`;
}

// 일별 막대 길이(0~100). 가장 많이 쓴 날이 100이고, 쓴 날은 아주 작아도 보이게 최소 2.
export function barPercent(cost: number, maxCost: number): number {
  if (cost <= 0 || maxCost <= 0) return 0;
  return Math.max((cost / maxCost) * 100, 2);
}

const PURPOSE_INFO: Record<string, { name: string; description: string }> = {
  agent: { name: "에이전트", description: "채팅과 예약 작업에서 요청을 처리한 모델 호출" },
  guard: {
    name: "감시 에이전트(승인 검사)",
    description: "앱 밖에 흔적을 남기는 작업 전에 요청과 맞는지 검사한 호출",
  },
  watch: { name: "조건 감시 판정", description: "조건 감시가 조건에 맞는지 판정한 호출" },
  eval: { name: "평가", description: "개발할 때 돌린 평가(eval) 실행 비용" },
};

export function purposeInfo(purpose: string): { name: string; description: string } {
  return PURPOSE_INFO[purpose] ?? { name: purpose, description: "" };
}

export function isUsageEmpty(usage: Pick<Usage, "total">): boolean {
  return usage.total.calls === 0;
}

// 네트워크가 끊기면 fetch가 영어 오류를 던지므로 한국어 문구로 바꾼다.
function networkError(): never {
  throw new Error(NETWORK_ERROR);
}

async function fetchUsage(days: number): Promise<Usage> {
  const { data, response } = await api
    .GET("/api/usage/daily", { params: { query: { days } } })
    .catch(networkError);
  if (data === undefined) throw new Error(`사용량을 불러오지 못했습니다 (오류 ${response.status})`);
  return data;
}

// 기간을 바꾸는 동안에는 이전 기간의 결과를 그대로 보여 준다(화면이 비었다가 다시 그려지지 않게).
export function useUsage(days: number) {
  return useQuery({
    queryKey: usageKeys.period(days),
    queryFn: () => fetchUsage(days),
    placeholderData: keepPreviousData,
  });
}
