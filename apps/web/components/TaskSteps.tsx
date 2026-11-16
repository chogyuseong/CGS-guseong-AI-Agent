"use client";

import { useTask } from "@/lib/chat";
import { StepTimeline } from "./StepTimeline";

// task 하나를 가져와 단계 목록을 보여 준다. task가 끝나지 않았으면 useTask가 1초마다 다시 가져온다.
export function TaskSteps({ taskId }: { taskId: number }) {
  const { data: task, isPending, isError } = useTask(taskId);
  if (isPending) return <p className="mt-2 text-xs text-muted-foreground">단계를 불러오는 중…</p>;
  if (isError) return <p className="mt-2 text-xs text-destructive">단계를 불러오지 못했습니다.</p>;
  return <StepTimeline steps={task.steps} />;
}
