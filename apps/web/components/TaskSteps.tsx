"use client";

import { useTask } from "@/lib/chat";
import { readMessageDraft } from "@/lib/stepOutput";
import { DraftCard } from "./DraftCard";
import { StepTimeline } from "./StepTimeline";
import { TaskApprovals } from "./TaskApprovals";

// task 하나를 가져와 단계 목록과 승인 카드, 메시지 초안 카드(4단계 보강)를 보여 준다.
// task가 끝나지 않았으면 useTask가 다시 가져온다(진행 중 1초, 승인 대기 3초).
export function TaskSteps({ taskId }: { taskId: number }) {
  const { data: task, isPending, isError } = useTask(taskId);
  if (isPending) return <p className="mt-2 text-xs text-muted-foreground">단계를 불러오는 중…</p>;
  if (isError) return <p className="mt-2 text-xs text-destructive">단계를 불러오지 못했습니다.</p>;
  return (
    <>
      <StepTimeline steps={task.steps} />
      <TaskApprovals task={task} />
      {task.steps.map((step) => {
        const draft = step.status === "done" ? readMessageDraft(step.tool_name, step.output) : null;
        return draft && <DraftCard key={step.id} draft={draft} />;
      })}
    </>
  );
}
