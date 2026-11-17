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
