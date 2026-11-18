import {
  barPercent,
  formatTokens,
  formatUsageDay,
  formatUsd,
  purposeInfo,
  type Usage,
} from "@/lib/usage";

// 비용 화면의 일별 표, 용도별·모델별 합계. 차트 라이브러리 없이 CSS 막대만 쓴다.
// 막대는 장식이고(aria-hidden), 같은 값이 표의 글자로 있어 화면 읽기 프로그램도 읽을 수 있다.
//
// 쓰는 곳: UsageReport.tsx(기간에 기록이 있을 때만). 데이터는 GET /api/usage/daily 응답(lib/usage.ts의 Usage) 그대로다.
// 이 파일은 서버를 부르지 않고 상태도 없다("use client" 없음, 클라이언트 컴포넌트 UsageReport 안에서 그려진다).
//
// 고칠 때 주의:
// - 용도 이름(agent·guard·watch·eval)의 한국어 설명은 lib/usage.ts의 PURPOSE_INFO다. 서버가 용도를 더하면
//   그 표에 넣는다(없으면 영어 이름이 그대로 보인다).
// - 금액·토큰 표기(소수 4자리, '미만')는 lib/usage.ts의 formatUsd·formatTokens가 정한다.
// - 테스트: components/UsageReport.test.tsx가 이 표·합계까지 함께 그려 본다.

// 기간 합계와 용도·모델별 합계가 같은 모양(cost_usd, 토큰, calls)이라 한 타입으로 쓴다.
type Totals = Usage["total"];

// 일별 표. 서버는 오래된 날부터 주지만, 위에서 아래로 읽는 표라 오늘이 맨 위에 오게 뒤집는다.
export function DailyTable({ usage }: { usage: Usage }) {
  const rows = [...usage.days].reverse();
  const maxCost = Math.max(0, ...rows.map((day) => day.cost_usd));
  return (
    <section aria-labelledby="usage-daily-title" className="flex min-w-0 flex-col gap-2">
      <h2 id="usage-daily-title" className="text-base font-semibold">
        일별 사용량
      </h2>
      <PeriodTotal days={rows.length} total={usage.total} />
      <table className="w-full table-fixed border-collapse text-sm">
        <caption className="sr-only">
          일별 사용량 표: 최근 {rows.length}일, 오늘부터. 막대는 기간 중 가장 많이 쓴 날 기준 길이
        </caption>
        <thead>
          <tr className="border-b text-xs text-muted-foreground">
            <th scope="col" className="w-[36%] py-2 pr-2 text-left font-medium">
              날짜
            </th>
            <th scope="col" className="py-2 pr-2 text-left font-medium">
              비용
            </th>
            <th scope="col" className="w-[28%] py-2 text-right font-medium">
              토큰
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((day) => (
            <DayRow key={day.date} day={day} maxCost={maxCost} />
          ))}
        </tbody>
      </table>
    </section>
  );
}

function PeriodTotal({ days, total }: { days: number; total: Totals }) {
  return (
    <p className="text-sm text-muted-foreground wrap-anywhere">
      최근 {days}일 합계{" "}
      <span className="font-semibold text-foreground tabular-nums">
        {formatUsd(total.cost_usd)}
      </span>{" "}
      · 토큰 {formatTokens(total.total_tokens)} · 호출 {formatTokens(total.calls)}회
    </p>
  );
}

function DayRow({ day, maxCost }: { day: Usage["days"][number]; maxCost: number }) {
  const width = barPercent(day.cost_usd, maxCost);
  return (
    <tr className="border-b last:border-b-0">
      <th scope="row" className="py-2 pr-2 text-left font-normal">
        {formatUsageDay(day.date)}
      </th>
      <td className="py-2 pr-2">
        <div className="flex min-w-0 flex-col gap-1">
          <span className="tabular-nums wrap-anywhere">{formatUsd(day.cost_usd)}</span>
          <div aria-hidden="true" className="h-2 w-full overflow-hidden rounded-full bg-muted">
            <div className="h-full rounded-full bg-primary" style={{ width: `${width}%` }} />
          </div>
        </div>
      </td>
      <td className="py-2 text-right tabular-nums wrap-anywhere">
        {formatTokens(day.total_tokens)}
      </td>
    </tr>
  );
}

// 용도별 합계. 서버가 쓰지 않은 용도도 0으로 늘 4개를 주므로 빈 목록 처리가 없다.
export function PurposeTotals({ purposes }: { purposes: Usage["by_purpose"] }) {
  return (
    <section aria-labelledby="usage-purpose-title" className="flex min-w-0 flex-col gap-2">
      <h2 id="usage-purpose-title" className="text-base font-semibold">
        용도별 합계
      </h2>
      <ul className="flex min-w-0 flex-col gap-2">
        {purposes.map((item) => {
          const info = purposeInfo(item.purpose);
          return (
            <TotalsCard
              key={item.purpose}
              name={info.name}
              description={info.description}
              totals={item}
            />
          );
        })}
      </ul>
    </section>
  );
}

// 모델별 합계. 이름은 서버가 준 모델 문자열 그대로라 길 수 있어 wrap-anywhere로 줄을 바꾼다.
export function ModelTotals({ models }: { models: Usage["by_model"] }) {
  if (models.length === 0) return null;
  return (
    <section aria-labelledby="usage-model-title" className="flex min-w-0 flex-col gap-2">
      <h2 id="usage-model-title" className="text-base font-semibold">
        모델별 합계
      </h2>
      <ul className="flex min-w-0 flex-col gap-2">
        {models.map((item) => (
          <TotalsCard key={item.model} name={item.model} totals={item} />
        ))}
      </ul>
    </section>
  );
}

type CardProps = { name: string; description?: string; totals: Totals };

function TotalsCard({ name, description, totals }: CardProps) {
  return (
    <li className="flex min-w-0 flex-col gap-1 rounded-xl border bg-card p-3 text-card-foreground">
      <div className="flex min-w-0 items-baseline justify-between gap-2">
        <span className="min-w-0 font-medium wrap-anywhere">{name}</span>
        <span className="shrink-0 font-semibold tabular-nums">{formatUsd(totals.cost_usd)}</span>
      </div>
      {description && <p className="text-xs text-muted-foreground">{description}</p>}
      <p className="text-xs text-muted-foreground tabular-nums wrap-anywhere">
        토큰 {formatTokens(totals.total_tokens)} (입력 {formatTokens(totals.input_tokens)} · 출력{" "}
        {formatTokens(totals.output_tokens)}) · 호출 {formatTokens(totals.calls)}회
      </p>
    </li>
  );
}
