"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import {
  DEFAULT_PERIOD,
  formatUsageDay,
  formatUsd,
  isUsageEmpty,
  PERIOD_OPTIONS,
  type Period,
  todayPercentText,
  todayRatio,
  type Usage,
  type UsageToday,
  useUsage,
} from "@/lib/usage";
import { cn } from "@/lib/utils";
import { DailyTable, ModelTotals, PurposeTotals } from "./UsageSections";

// 비용 화면(9단계 2번): 오늘 쓴 금액·한도·남은 금액, 일별 토큰과 비용, 용도별·모델별 합계.
// 보기만 하는 화면이다. 한도를 바꾸는 기능은 명세에 없어 만들지 않았다(한도는 서버 설정).
//
// 쓰는 곳: app/(tabs)/usage/page.tsx(설정 탭의 "비용" 링크로 들어온다. 하단 탭은 '설정'이 현재 탭).
// 서버 호출: lib/usage.ts의 useUsage → GET /api/usage/daily?days=N(apps/api/app/routers/usage.py,
//   합계 계산은 app/usage_report.py). 기간을 바꾸는 동안에는 이전 결과를 그대로 보인다(keepPreviousData).
// 근거: BUILD_SPEC 9단계 2번, DECISIONS 2026-11-17 비용 화면 결정(today.exceeded면 빨간 막대와 alert 문구).
//
// 고칠 때 주의:
// - 한도는 서버 .env의 DAILY_COST_LIMIT_USD다. 화면은 today.limit_usd를 그대로 보여 줄 뿐 계산하지 않는다.
// - "서울 시간 0시"는 서버의 하루 비용 상한 원장(daily_budgets)이 서울 날짜로 나뉘는 것과 맞물린다.
// - ExceededNotice의 "예약 작업과 조건 감시도 모델을 부르지 않습니다"는 6단계 보강의 budget_paused 동작
//   (AutomationList.tsx BudgetPausedNotice)과 같은 사실이다. 한쪽 동작이 바뀌면 둘 다 고친다.
// - 테스트: components/UsageReport.test.tsx, e2e/usage.spec.ts, e2e/widths.spec.ts(한도 초과·긴 모델 이름).
export function UsageReport() {
  const [days, setDays] = useState<Period>(DEFAULT_PERIOD);
  const query = useUsage(days);
  return (
    <div className="flex min-w-0 flex-col gap-4">
      <PeriodPicker value={days} onChange={setDays} />
      <UsageBody
        days={days}
        data={query.data}
        pending={query.isPending}
        switching={query.isPlaceholderData}
        error={query.isError ? query.error.message : null}
        fetching={query.isFetching}
        onReload={() => void query.refetch()}
      />
    </div>
  );
}

// 기간 버튼(7·14·30일, lib/usage.ts PERIOD_OPTIONS). 서버는 1~90일을 받으므로 선택지는 그 안에서만 늘린다.
// 선택 상태는 aria-pressed로 알린다.
function PeriodPicker({ value, onChange }: { value: Period; onChange: (days: Period) => void }) {
  return (
    <fieldset className="flex min-w-0 flex-col gap-2">
      <legend className="sr-only">기간</legend>
      <p className="text-sm text-muted-foreground">
        기간을 고르세요. 날짜는 서울 시간 0시 기준입니다.
      </p>
      <div className="grid grid-cols-3 gap-2">
        {PERIOD_OPTIONS.map((days) => (
          <Button
            key={days}
            type="button"
            variant={value === days ? "default" : "outline"}
            aria-pressed={value === days}
            onClick={() => onChange(days)}
            className="h-11 min-w-0 text-sm"
          >
            {days}일
          </Button>
        ))}
      </div>
    </fieldset>
  );
}

type BodyProps = {
  days: number;
  data: Usage | undefined;
  pending: boolean;
  switching: boolean;
  error: string | null;
  fetching: boolean;
  onReload: () => void;
};

// 본문. pending은 처음 한 번도 받지 못한 상태, switching은 기간을 바꿔 이전 기간 결과를 임시로 보이는 상태다.
// 다시 불러오기가 실패해도 받아 둔 data가 있으면 오류와 함께 그대로 보여 준다.
function UsageBody({ days, data, pending, switching, error, fetching, onReload }: BodyProps) {
  if (pending) return <p className="text-sm text-muted-foreground">불러오는 중…</p>;
  const loadError = error !== null && (
    <LoadError message={error} fetching={fetching} onReload={onReload} />
  );
  if (data === undefined) return loadError;
  return (
    <>
      {loadError}
      <TodayCard today={data.today} />
      {switching && (
        <p role="status" className="text-sm text-muted-foreground">
          최근 {days}일 사용량을 불러오는 중…
        </p>
      )}
      {isUsageEmpty(data) ? (
        <EmptyGuide days={data.days.length} />
      ) : (
        <>
          <DailyTable usage={data} />
          <PurposeTotals purposes={data.by_purpose} />
          <ModelTotals models={data.by_model} />
        </>
      )}
    </>
  );
}

type LoadErrorProps = { message: string; fetching: boolean; onReload: () => void };

function LoadError({ message, fetching, onReload }: LoadErrorProps) {
  return (
    <div className="flex flex-col gap-2 rounded-lg border border-destructive/40 bg-destructive/5 p-3">
      <p role="alert" className="text-sm text-destructive wrap-anywhere">
        {message}
      </p>
      <Button
        type="button"
        variant="outline"
        onClick={onReload}
        disabled={fetching}
        className="h-12 w-full min-w-0 text-base"
      >
        {fetching ? "다시 불러오는 중…" : "다시 불러오기"}
      </Button>
    </div>
  );
}

function TodayCard({ today }: { today: UsageToday }) {
  const percent = todayPercentText(today);
  return (
    <section
      aria-labelledby="usage-today-title"
      className="flex min-w-0 flex-col gap-3 rounded-xl border bg-card p-4 text-card-foreground"
    >
      <div className="flex min-w-0 flex-wrap items-baseline justify-between gap-x-2">
        <h2 id="usage-today-title" className="text-base font-semibold">
          오늘
        </h2>
        <span className="text-sm text-muted-foreground">{formatUsageDay(today.date)}</span>
      </div>
      <dl className="flex flex-col gap-1 text-sm">
        <AmountRow label="쓴 금액" value={today.cost_usd} />
        <AmountRow label="하루 한도" value={today.limit_usd} />
        <AmountRow label="남은 금액" value={today.remaining_usd} strong />
      </dl>
      <LimitBar today={today} percent={percent} />
      {today.exceeded && <ExceededNotice />}
    </section>
  );
}

function AmountRow({ label, value, strong }: { label: string; value: number; strong?: boolean }) {
  return (
    <div className="flex min-w-0 items-baseline justify-between gap-2">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className={cn("tabular-nums", strong && "text-base font-semibold")}>
        {formatUsd(value)}
      </dd>
    </div>
  );
}

// 오늘 한도 사용률 막대. 비율 계산과 100% 상한 처리는 lib/usage.ts의 todayRatio·todayPercentText가 한다.
function LimitBar({ today, percent }: { today: UsageToday; percent: string }) {
  const ratio = todayRatio(today);
  return (
    <div className="flex flex-col gap-1">
      <div
        role="progressbar"
        aria-label="오늘 한도 사용률"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.floor(ratio * 100)}
        aria-valuetext={`${percent} 사용`}
        className="h-3 w-full overflow-hidden rounded-full bg-muted"
      >
        <div
          className={cn("h-full rounded-full", today.exceeded ? "bg-destructive" : "bg-primary")}
          style={{ width: `${ratio * 100}%` }}
        />
      </div>
      <p className="text-xs text-muted-foreground">오늘 한도의 {percent}를 썼습니다.</p>
    </div>
  );
}

function ExceededNotice() {
  return (
    <div
      role="alert"
      className="flex flex-col gap-1 rounded-lg border border-destructive/40 bg-destructive/5 p-3 text-sm text-destructive"
    >
      <p className="font-medium">오늘 한도를 넘어 새 작업이 모델을 부르지 않습니다.</p>
      <p>예약 작업과 조건 감시도 모델을 부르지 않습니다. 서울 시간 0시가 지나면 다시 씁니다.</p>
    </div>
  );
}

function EmptyGuide({ days }: { days: number }) {
  return (
    <div className="flex flex-col gap-2 rounded-xl border bg-card p-4 text-card-foreground">
      <p className="text-base font-medium">이 기간에는 모델을 부른 기록이 없습니다.</p>
      <p className="text-sm text-muted-foreground">
        최근 {days}일 동안 채팅·예약 작업·조건 감시가 모델을 부르지 않았습니다. 모델을 부르면 날짜별
        토큰과 비용이 여기에 쌓입니다.
      </p>
    </div>
  );
}
