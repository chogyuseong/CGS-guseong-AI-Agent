"use client";

import { type ReactNode, useState } from "react";
import {
  activeCountText,
  type Schedule,
  useSchedules,
  useWatches,
  type Watch,
} from "@/lib/automations";
import { type Feedback, FeedbackLine, LoadError, type Report } from "./AutomationControls";
import { ScheduleItem, WatchItem } from "./AutomationItems";
import { BriefingSetup } from "./BriefingSetup";

// 자동화 탭(6단계): 예약 작업과 조건 감시의 목록, 켜기·끄기, 삭제(화면 안 확인 한 번), 마지막 실행 결과.
// 만들기·내용 고치기는 없다(명세 화면 항목에 없고 API도 없다). 예약과 감시는 채팅에서 말로 만든다.
//
// 쓰는 곳: app/(tabs)/automation/page.tsx. 서버 호출은 lib/automations.ts(useSchedules → GET /api/schedules,
//   useWatches → GET /api/watches, 켜기·끄기·삭제는 AutomationControls, 브리핑 켜기는 BriefingSetup).
// 근거: BUILD_SPEC 8절, DECISIONS 2026-11-17 "자동화 탭"·화면 위 안내 문장·6단계 보강(브리핑은 사용자가 켤 때만,
//   budget_paused 표시).
//
// 고칠 때 주의:
// - WATCH_EMPTY의 "30분", "30일"과 SCHEDULE_EMPTY의 "하루 한 번"은 서버 apps/api/app/automation/rules.py의
//   MIN_WATCH_INTERVAL_MINUTES·WATCH_LIFETIME·예약 구조(시각 하나 + 요일)와 글자로 맞물려 있다. 값이 바뀌면 같이 고친다.
//   활성 개수 한도는 글자로 적지 않고 서버의 max_active를 그대로 쓴다.
// - '예약 작업' 대화방 이름은 rules.py AUTOMATION_CONVERSATION_TITLE·lib/automations.ts와 같은 글자다.
// - 테스트: components/AutomationList.test.tsx, components/BriefingSetup.test.tsx, e2e/automation.spec.ts.
//
// 안내 줄(feedback)은 탭 전체에 하나다. 아래 어느 항목에서 눌렀든 결과가 같은 자리에 뜬다.
export function AutomationList() {
  const [feedback, setFeedback] = useState<Feedback | null>(null);
  return (
    <div className="flex min-w-0 flex-col gap-4">
      <Guide />
      <BudgetPausedNotice />
      <FeedbackLine feedback={feedback} />
      <ScheduleSection report={setFeedback} />
      <WatchSection report={setFeedback} />
    </div>
  );
}

// 화면 위에 늘 보이는 안내(DECISIONS 2026-11-17: 조회만 함, 결과는 '예약 작업' 대화방, 만들기는 채팅에서).
// 두 번째 줄은 절대 원칙 7(예약·감시 모델에는 READ 도구만)을 사용자 말로 옮긴 것이다.
const GUIDE_LINES = [
  "아침 브리핑은 직접 켜야 실행됩니다. 계정을 만들었다고 저절로 돌지 않습니다.",
  "자동 작업은 조회만 합니다. 일정 등록처럼 무언가를 바꾸는 일은 결과에 제안 버튼으로 알려 드립니다. 누르면 입력창에 문장이 채워지고, 직접 보내야 평소처럼 승인을 거칩니다.",
  "예약 작업 결과와 조건 감시 알림은 '예약 작업' 대화방에 남습니다.",
  "예약과 감시는 채팅에서 말로 만듭니다. 이 화면에서는 켜기·끄기와 삭제만 할 수 있습니다.",
];

// 6단계 보강: 하루 비용 한도에 닿으면 서버가 budget_paused를 준다. 예약·감시는 건너뛰고 서울 자정이 지나면 다시 돈다.
// 두 목록 쿼리를 섹션과 같은 queryKey로 부르므로 요청이 더 생기지 않는다(TanStack Query 캐시 공유).
// 둘 중 하나라도 true면 보인다. 한도 값 자체는 비용 화면(UsageReport)에서 보여 준다.
function BudgetPausedNotice() {
  const schedules = useSchedules();
  const watches = useWatches();
  if (!schedules.data?.budget_paused && !watches.data?.budget_paused) return null;
  return (
    <p
      role="status"
      aria-label="비용 한도"
      className="rounded-xl border border-warning/40 bg-warning/5 p-3 text-sm"
    >
      오늘 비용 한도에 닿아 자동 작업이 쉬는 중입니다. 예약 실행과 감시 확인은 건너뛰고, 내일(서울
      자정 이후) 다시 실행됩니다. 한도는 비용 화면에서 확인할 수 있습니다.
    </p>
  );
}

function Guide() {
  return (
    <ul className="flex list-disc flex-col gap-1 rounded-xl border bg-muted/40 p-3 pl-8 text-sm text-muted-foreground">
      {GUIDE_LINES.map((line) => (
        <li key={line}>{line}</li>
      ))}
    </ul>
  );
}

type SectionShellProps = {
  id: string;
  title: string;
  query: {
    isPending: boolean;
    isError: boolean;
    isFetching: boolean;
    error: Error | null;
    refetch: () => unknown;
  };
  summary: ReactNode;
  children: ReactNode;
};

// 섹션 틀: 제목, 불러오는 중·오류(다시 불러오기), 개수 줄, 내용.
function SectionShell({ id, title, query, summary, children }: SectionShellProps) {
  const loadError = query.isError && query.error && (
    <LoadError
      message={query.error.message}
      fetching={query.isFetching}
      onReload={() => void query.refetch()}
    />
  );
  return (
    <section aria-labelledby={id} className="flex min-w-0 flex-col gap-2">
      <h2 id={id} className="text-base font-semibold">
        {title}
      </h2>
      {query.isPending && <p className="text-sm text-muted-foreground">불러오는 중…</p>}
      {loadError}
      {summary}
      {children}
    </section>
  );
}

// 켜진 개수 / 최대. 꺼진 항목은 자리를 차지하지 않는다(rules.py와 같은 기준). 다 차면 미리 알리지만
// 실제 거부는 서버가 한다(409 limit_reached → automationErrorText 문구).
function CountLine({ noun, count, max }: { noun: string; count: number; max: number }) {
  return (
    <div className="flex flex-col gap-1 text-sm">
      <p className="font-medium">{activeCountText(noun, count, max)}</p>
      {count >= max && (
        <p className="text-destructive">
          켤 수 있는 개수가 다 찼습니다. 새로 만들거나 다시 켜려면 먼저 하나를 꺼 주세요.
        </p>
      )}
    </div>
  );
}

function EmptyGuide({ title, lines }: { title: string; lines: string[] }) {
  return (
    <div className="flex flex-col gap-2 rounded-xl border bg-card p-4 text-card-foreground">
      <p className="text-base font-medium">{title}</p>
      {lines.map((line) => (
        <p key={line} className="text-sm text-muted-foreground">
          {line}
        </p>
      ))}
    </div>
  );
}

const SCHEDULE_EMPTY = [
  "채팅에서 '매주 일요일 밤 9시에 다음 주 일정 요약해줘'처럼 말하면 만들어집니다.",
  "하루 한 번보다 자주 실행할 수는 없습니다.",
];

// 브리핑이 바뀌면(켜기·시각 변경) 입력칸을 서버 값으로 다시 맞춘다.
// BriefingSetup은 시각을 useState 초기값으로만 받으므로, 이 값을 React key로 줘서 바뀔 때 새로 그리게 한다.
function briefingKey(schedules: Schedule[]): string {
  const briefing = schedules.find((item) => item.is_briefing);
  return briefing ? `${briefing.id}-${briefing.time_of_day}-${briefing.is_active}` : "none";
}

// 예약 섹션. 브리핑 설정 카드는 브리핑이 없어도 늘 보인다(사용자가 켜야 생기므로 켜는 자리가 있어야 한다).
function ScheduleSection({ report }: { report: Report }) {
  const query = useSchedules();
  const data = query.data;
  const count = data?.schedules.filter((item: Schedule) => item.is_active).length ?? 0;
  return (
    <SectionShell
      id="schedules-title"
      title="예약 작업"
      query={query}
      summary={data && <CountLine noun="예약 작업" count={count} max={data.max_active} />}
    >
      {data && (
        <BriefingSetup
          key={briefingKey(data.schedules)}
          briefing={data.schedules.find((item) => item.is_briefing)}
          maxActive={data.max_active}
        />
      )}
      {data && data.schedules.length === 0 && (
        <EmptyGuide title="아직 예약 작업이 없습니다." lines={SCHEDULE_EMPTY} />
      )}
      {data && data.schedules.length > 0 && (
        <ul className="flex min-w-0 flex-col gap-2">
          {data.schedules.map((schedule) => (
            <ScheduleItem
              key={schedule.id}
              schedule={schedule}
              maxActive={data.max_active}
              report={report}
            />
          ))}
        </ul>
      )}
    </SectionShell>
  );
}

const WATCH_EMPTY = [
  "채팅에서 '내일 서울에 비 올 확률이 60% 넘으면 알려줘'처럼 말하면 만들어집니다.",
  "30분보다 자주 확인하지 않고, 만든 지 30일이 지나면 만료됩니다.",
];

function WatchSection({ report }: { report: Report }) {
  const query = useWatches();
  const data = query.data;
  const count = data?.watches.filter((item: Watch) => item.is_active).length ?? 0;
  return (
    <SectionShell
      id="watches-title"
      title="조건 감시"
      query={query}
      summary={data && <CountLine noun="조건 감시" count={count} max={data.max_active} />}
    >
      {data && data.watches.length === 0 && (
        <EmptyGuide title="아직 조건 감시가 없습니다." lines={WATCH_EMPTY} />
      )}
      {data && data.watches.length > 0 && (
        <ul className="flex min-w-0 flex-col gap-2">
          {data.watches.map((watch) => (
            <WatchItem key={watch.id} watch={watch} maxActive={data.max_active} report={report} />
          ))}
        </ul>
      )}
    </SectionShell>
  );
}
