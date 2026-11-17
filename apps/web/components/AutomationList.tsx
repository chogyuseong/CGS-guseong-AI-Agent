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

const GUIDE_LINES = [
  "아침 브리핑은 직접 켜야 실행됩니다. 계정을 만들었다고 저절로 돌지 않습니다.",
  "자동 작업은 조회만 합니다. 일정 등록처럼 무언가를 바꾸는 일은 결과에 제안 버튼으로 알려 드립니다. 누르면 입력창에 문장이 채워지고, 직접 보내야 평소처럼 승인을 거칩니다.",
  "예약 작업 결과와 조건 감시 알림은 '예약 작업' 대화방에 남습니다.",
  "예약과 감시는 채팅에서 말로 만듭니다. 이 화면에서는 켜기·끄기와 삭제만 할 수 있습니다.",
];

// 6단계 보강: 하루 비용 한도에 닿으면 서버가 budget_paused를 준다. 예약·감시는 건너뛰고 서울 자정이 지나면 다시 돈다.
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
function briefingKey(schedules: Schedule[]): string {
  const briefing = schedules.find((item) => item.is_briefing);
  return briefing ? `${briefing.id}-${briefing.time_of_day}-${briefing.is_active}` : "none";
}

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
