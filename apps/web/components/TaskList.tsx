// 작업 기록 탭의 task 목록(app/(tabs)/tasks/page.tsx).
// - 쓰는 lib: lib/chat.ts useTasks(GET /api/tasks?limit=50, 끝나지 않은 task가 있으면 폴링, 간격은 lib/polling.ts).
//   항목을 펼치면 TaskSteps가 그 task를 따로 가져온다(GET /api/tasks/{id}). 대기 중인 승인은 여기서도 결정할 수 있다
//   (DECISIONS 2026-11-17 화면 절).
// 고칠 때 주의: STATUS_LABELS는 서버 TaskStatus 12가지(BUILD_SPEC 9.2)와 글자로 맞물린다. Record라 상태가 늘면
//   tsc가 알려 준다. docs/STATE_MACHINES.md task 표도 같이 본다. e2e widths.spec.ts가 이 탭에서 단계 버튼을 찾는다.
"use client";

import { ChevronDown } from "lucide-react";
import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { type TaskStatus, type TaskSummary, useTasks } from "@/lib/chat";
import { cn } from "@/lib/utils";
import { TaskSteps } from "./TaskSteps";

const STATUS_LABELS: Record<TaskStatus, string> = {
  queued: "대기",
  running: "진행 중",
  waiting_input: "답변 필요",
  waiting_approval: "승인 대기",
  cancel_requested: "취소 중",
  reconciling: "결과 확인 중",
  done: "완료",
  partially_done: "일부 완료",
  failed: "실패",
  cancelled: "취소됨",
  budget_exceeded: "상한 초과",
  needs_review: "확인 필요",
};

// 기기 시간대와 상관없이 서울 시각으로 보인다(앱의 다른 시각 표시와 같은 기준).
function formatTime(iso: string): string {
  return new Date(iso).toLocaleString("ko-KR", {
    timeZone: "Asia/Seoul",
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

// 작업 기록 탭: 최신순 task 목록. 항목을 누르면 그 task의 단계를 펼친다.
export function TaskList() {
  const { data: tasks, isPending, isError } = useTasks();
  if (isPending) return <p className="text-sm text-muted-foreground">불러오는 중…</p>;
  if (isError) return <p className="text-sm text-destructive">작업 기록을 불러오지 못했습니다.</p>;
  if (tasks.length === 0)
    return <p className="text-sm text-muted-foreground">아직 작업이 없습니다.</p>;
  return (
    <ul className="flex flex-col gap-2">
      {tasks.map((task) => (
        <TaskItem key={task.id} task={task} />
      ))}
    </ul>
  );
}

// 한 줄: 요청 문장(예약 task처럼 내 메시지가 없으면 "(요청 문장 없음)"), 상태 배지, 단계 수, 만든 시각.
// 빨간 배지는 실패·상한 초과·확인 필요만이다. 상태는 배지 글자로도 보인다(색만으로 표현하지 않음).
// 펼칠 때만 TaskSteps를 그려 접힌 항목마다 task를 가져오지 않는다.
function TaskItem({ task }: { task: TaskSummary }) {
  const [open, setOpen] = useState(false);
  const failed = ["failed", "budget_exceeded", "needs_review"].includes(task.status);
  return (
    <li className="rounded-lg border">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
        className="flex min-h-11 w-full flex-col gap-1 px-3 py-2 text-left"
      >
        <span className="line-clamp-2 text-base">{task.request_text ?? "(요청 문장 없음)"}</span>
        <span className="flex items-center gap-2 text-xs text-muted-foreground">
          <Badge variant={failed ? "destructive" : "secondary"}>{STATUS_LABELS[task.status]}</Badge>
          <span>단계 {task.step_count}개</span>
          <span>{formatTime(task.created_at)}</span>
          <ChevronDown aria-hidden="true" className={cn("ml-auto size-4", open && "rotate-180")} />
        </span>
      </button>
      {open && (
        <div className="border-t px-3 pb-3">
          <TaskSteps taskId={task.id} />
        </div>
      )}
    </li>
  );
}
