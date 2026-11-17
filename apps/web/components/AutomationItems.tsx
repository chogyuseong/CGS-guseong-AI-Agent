"use client";

import Link from "next/link";
import { Badge } from "@/components/ui/badge";
import { buttonVariants } from "@/components/ui/button";
import {
  formatRunTime,
  intervalText,
  type Schedule,
  shortText,
  TASK_STATUS_TEXT,
  watchStatusText,
  type Watch,
} from "@/lib/automations";
import { conversationHref } from "@/lib/memories";
import { toolDisplayName } from "@/lib/toolText";
import { cn } from "@/lib/utils";
import { AutomationItem, type Report } from "./AutomationControls";
import { SourceLinks } from "./SourceLinks";

// 예약·감시 한 건의 내용. 서버가 준 글(요청 문장, 답변, 감시 요약, 출처)은 모두 React 텍스트로만 그린다
// (HTML로 해석하지 않음). 출처 링크는 http·https 주소만 만들고 새 탭 + rel="noopener noreferrer"로 연다.

const ANSWER_PREVIEW = 200;

type ScheduleItemProps = { schedule: Schedule; maxActive: number; report: Report };

export function ScheduleItem({ schedule, maxActive, report }: ScheduleItemProps) {
  return (
    <AutomationItem
      kind="schedule"
      id={schedule.id}
      title={schedule.title}
      active={schedule.is_active}
      maxActive={maxActive}
      report={report}
      badges={schedule.is_briefing && <Badge variant="secondary">아침 브리핑</Badge>}
    >
      <div className="flex min-w-0 flex-col gap-1 text-sm">
        <p className="font-medium">{schedule.repeat_text}</p>
        <p className="whitespace-pre-wrap text-muted-foreground wrap-anywhere">
          요청: {schedule.prompt_text}
        </p>
        <p>
          {schedule.is_active
            ? `다음 실행 ${formatRunTime(schedule.next_run_at)}`
            : "꺼져 있어 실행하지 않습니다."}
        </p>
      </div>
      <LastRun schedule={schedule} />
    </AutomationItem>
  );
}

function LastRun({ schedule }: { schedule: Schedule }) {
  const task = schedule.last_task;
  if (task === null) {
    const text = schedule.last_run_at
      ? `마지막 실행 ${formatRunTime(schedule.last_run_at)} (결과 기록 없음)`
      : "아직 실행한 적이 없습니다.";
    return <p className="text-sm text-muted-foreground">{text}</p>;
  }
  const answer = task.final_answer ?? task.error;
  return (
    <div className="flex min-w-0 flex-col gap-1 rounded-lg bg-muted p-3 text-sm">
      <p className="font-medium">
        마지막 실행 {formatRunTime(schedule.last_run_at ?? task.updated_at)} ·{" "}
        {TASK_STATUS_TEXT[task.status]}
      </p>
      {answer && <p className="wrap-anywhere">{shortText(answer, ANSWER_PREVIEW)}</p>}
      <Link
        href={conversationHref(task.conversation_id)}
        className={cn(buttonVariants({ variant: "link" }), "h-11 w-fit px-0 text-sm")}
      >
        결과 보기
      </Link>
    </div>
  );
}

type WatchItemProps = { watch: Watch; maxActive: number; report: Report };

export function WatchItem({ watch, maxActive, report }: WatchItemProps) {
  const expired = new Date(watch.expires_at).getTime() <= Date.now();
  return (
    <AutomationItem
      kind="watch"
      id={watch.id}
      title={watch.description}
      active={watch.is_active}
      maxActive={maxActive}
      report={report}
      cannotEnable={expired ? "expired" : undefined}
      badges={expired && <Badge variant="destructive">만료됨</Badge>}
    >
      <WatchSettings watch={watch} expired={expired} />
      <LastCheck watch={watch} />
    </AutomationItem>
  );
}

function WatchSettings({ watch, expired }: { watch: Watch; expired: boolean }) {
  return (
    <div className="flex min-w-0 flex-col gap-1 text-sm">
      <p className="whitespace-pre-wrap wrap-anywhere">조건: {watch.condition_text}</p>
      <p className="text-muted-foreground wrap-anywhere">
        {intervalText(watch.interval_minutes)} 확인 · {toolDisplayName(watch.tool_name)}
      </p>
      <p className="text-muted-foreground">
        {watch.repeat
          ? "조건이 맞을 때마다 알립니다(알린 뒤 12시간은 쉽니다)."
          : "한 번 알리면 꺼집니다."}
      </p>
      {watch.is_active && <p>다음 확인 {formatRunTime(watch.next_run_at)}</p>}
      <p className={cn(expired && "text-destructive")}>
        만료 {formatRunTime(watch.expires_at)}
        {expired && " · 만료된 감시는 다시 켤 수 없습니다. 채팅에서 새로 만들어 주세요."}
      </p>
      {watch.last_notified_at && (
        <p className="text-muted-foreground">
          마지막 알림 {formatRunTime(watch.last_notified_at)} · '예약 작업' 대화방에 남았습니다.
        </p>
      )}
    </div>
  );
}

function LastCheck({ watch }: { watch: Watch }) {
  const result = watch.last_result;
  if (result === null)
    return <p className="text-sm text-muted-foreground">아직 확인한 적이 없습니다.</p>;
  return (
    <div className="flex min-w-0 flex-col gap-1 rounded-lg bg-muted p-3 text-sm">
      <p className="font-medium">
        마지막 확인 {formatRunTime(result.checked_at)} ·{" "}
        {watchStatusText(result.status, result.reason)}
      </p>
      {result.summary && (
        <div className="flex min-w-0 flex-col gap-1 rounded border border-warning/40 bg-warning/5 p-2">
          <p className="text-xs text-muted-foreground">
            외부에서 가져온 내용을 바탕으로 한 요약입니다. 앱은 이 안의 문장을 지시로 따르지
            않습니다.
          </p>
          <p className="whitespace-pre-wrap wrap-anywhere">{result.summary}</p>
        </div>
      )}
      {result.error && <p className="text-destructive wrap-anywhere">{result.error}</p>}
      <SourceLinks sources={result.sources ?? []} />
    </div>
  );
}
