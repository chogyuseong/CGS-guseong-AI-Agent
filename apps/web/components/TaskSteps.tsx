// task 하나의 단계 목록·승인 카드·메시지 초안 카드(답변 아래에서는 보통 끝난 task, 작업 기록 탭에서는 진행 중일 수도 있다).
// - 쓰는 곳: MessageList의 답변 말풍선 아래, TaskList의 펼친 항목.
// - 쓰는 lib: lib/chat.ts useTask(GET /api/tasks/{task_id}), lib/stepOutput.ts readMessageDraft.
// - 2026-11-19 답변 아래에 '함께 보낸 기억'(MemoriesSent, 서버 memories_sent)을 접어서 보인다.
// 고칠 때 주의: 초안 카드는 done 단계에만 그린다. 실패·불명 단계의 초안 모양 출력을 "만든 초안"처럼 보이지 않게 하려는 것.
"use client";

import { useTask } from "@/lib/chat";
import { readMessageDraft } from "@/lib/stepOutput";
import { DraftCard } from "./DraftCard";
import { MemoriesSent } from "./MemoriesSent";
import { StepTimeline } from "./StepTimeline";
import { TaskApprovals } from "./TaskApprovals";

// task 하나를 가져와 단계 목록과 승인 카드, 메시지 초안 카드(4단계 보강)를 보여 준다.
// task가 끝나지 않았으면 useTask가 다시 가져온다(간격은 lib/polling.ts: 실행 중 1초부터 늘림, 승인 대기 15초).
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
      <MemoriesSent memories={task.memories_sent ?? []} />
    </>
  );
}
