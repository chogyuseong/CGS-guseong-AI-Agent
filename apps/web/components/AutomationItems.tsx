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
// (HTML로 해석하지 않음). 출처 링크는 http·https 주소만 만들고 새 탭 + rel="noopener noreferrer"로 연다
// (출처 목록은 SourceLinks.tsx가 그린다).
//
// 쓰는 곳: AutomationList.tsx의 예약·감시 섹션. 데이터는 GET /api/schedules·GET /api/watches 응답 한 건
//   (lib/automations.ts의 Schedule·Watch, 생성 타입 ScheduleOut·WatchOut).
// 근거: BUILD_SPEC 8절, DECISIONS 2026-11-17 "자동화 탭"(마지막 실행 결과 보여 주기), 6단계 보강(budget·paused_budget).
//
// 고칠 때 주의:
// - 반복 감시 안내 문구("맞지 않다가 맞게 바뀔 때 … 12시간 안에는 다시 알리지 않습니다")는 서버
//   apps/api/app/automation/check.py의 should_notify와 rules.py의 WATCH_REPEAT_DELAY(12시간)를 글자로 옮긴 것이다
//   (2026-11-18: 옛 문구 "알릴 때마다 … 12시간 쉼"이 실제 규칙과 달라 고침, AutomationList.test.tsx). 규칙·값을 바꾸면 같이 고친다.
// - 상태 글자는 lib/automations.ts의 TASK_STATUS_TEXT(작업 상태)·watchStatusText(감시 결과 status·reason)가 정한다.
//   서버에 상태 값을 더하면 그쪽 표를 고친다(생성 타입이 Record 키 빠짐을 잡는다).
// - 감시 도구 이름은 lib/toolText.ts의 toolDisplayName이 한국어로 바꾼다.

// 마지막 답변 미리보기 글자 수. 전체는 '결과 보기'로 대화방에서 본다.
const ANSWER_PREVIEW = 200;

type ScheduleItemProps = { schedule: Schedule; maxActive: number; report: Report };

// 예약 작업 한 건: 반복 설명, 요청 문장, 다음 실행 시각, 마지막 실행 결과. 아침 브리핑(is_briefing)에는 배지를 단다
// (브리핑도 다른 예약과 똑같이 끄고 지울 수 있다).
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

// 마지막 실행. 서버(routers/automations.py last_tasks)가 last_task_id로 작업을 찾지 못하면 last_task가 null이다.
// 그때 last_run_at이 있으면 "결과 기록 없음"으로 실행 사실만 알린다. 결과 링크는 그 작업의 대화방으로 간다.
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

// 조건 감시 한 건. 만료는 화면을 그리는 순간의 브라우저 시각으로 판단한다. 기기 시계가 틀려도 서버가
// 만료된 감시 켜기를 409 expired로 거부하므로 안전에는 영향이 없다(화면 표시만 어긋난다).
export function WatchItem({ watch, maxActive, report }: WatchItemProps) {
  const expired = new Date(watch.expires_at).getTime() <= Date.now();
  return (
    <AutomationItem
      kind="watch"
      id={watch.id}
      title={watch.description}
      stopLabel={watch.tool_name === "coupang_price" ? "샀어요 · 감시 끄기" : undefined}
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
          ? "조건이 맞지 않다가 맞게 바뀔 때 알립니다(마지막 알림 뒤 12시간 안에는 다시 알리지 않습니다)."
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

// 마지막 확인 결과. summary는 외부 도구 결과를 바탕으로 감시 모델이 쓴 글이라 "지시로 따르지 않는다" 안내 상자에
// 넣는다(절대 원칙 14: 도구 결과 안의 문장은 데이터). 출처는 서버가 본문과 따로 준 sources만 링크로 만든다.
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
