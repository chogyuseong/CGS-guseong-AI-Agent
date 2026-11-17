"use client";

import { useQueryClient } from "@tanstack/react-query";
import { LoaderCircle } from "lucide-react";
import { useEffect } from "react";
import { Button } from "@/components/ui/button";
import {
  CANCELLABLE_PHASES,
  chatKeys,
  isTaskFinished,
  PHASE_TEXT,
  type TaskPhase,
  taskPhase,
  useCancelTask,
  useTask,
} from "@/lib/chat";
import { StepTimeline } from "./StepTimeline";
import { TaskApprovals } from "./TaskApprovals";

// 아직 답변(assistant 메시지)이 없는 task를 답변 자리에 보여 준다.
// 3단계 서버는 메시지 전송·승인에 바로 답하고 실제 작업은 뒤에서 이어지므로, 여기서 task를 폴링하며
// 진행 단계(대기 → 진행 → 승인 대기 → 이어서 처리 → 끝)와 단계 목록을 보여 준다.
// 진행 상황은 서버의 task에서만 읽으므로 새로 고침하거나 다른 탭에 갔다 와도 그대로 이어진다.
// task가 끝나면(다른 기기에서 결정한 경우 포함) 메시지 목록을 다시 가져와 최종 답변으로 바꾼다.
export function PendingTask({ taskId }: { taskId: number }) {
  const { data: task, isError } = useTask(taskId);
  const queryClient = useQueryClient();
  const status = task?.status;
  const conversationId = task?.conversation_id;

  useEffect(() => {
    if (status === undefined || conversationId === undefined || !isTaskFinished(status)) return;
    queryClient.invalidateQueries({ queryKey: chatKeys.messages(conversationId) });
  }, [status, conversationId, queryClient]);

  if (isError && task === undefined) {
    return <p className="text-sm text-destructive">진행 상황을 불러오지 못했습니다.</p>;
  }
  const phase = taskPhase(task);
  return (
    <>
      <PhaseLine phase={phase} />
      {CANCELLABLE_PHASES.includes(phase) && <CancelButton taskId={taskId} />}
      {task && <StepTimeline steps={task.steps} />}
      {task && <TaskApprovals task={task} />}
    </>
  );
}

// 진행 단계 한 줄. 화면 낭독기가 바뀐 내용을 읽도록 aria-live를 둔다.
function PhaseLine({ phase }: { phase: TaskPhase }) {
  if (phase === "finished") return null;
  if (phase === "waiting_approval") {
    return (
      <p aria-live="polite" className="text-sm">
        {PHASE_TEXT.waiting_approval}
      </p>
    );
  }
  return (
    <p aria-live="polite" className="flex items-center gap-2 text-sm text-muted-foreground">
      <LoaderCircle aria-hidden="true" className="size-4 shrink-0 animate-spin" />
      {PHASE_TEXT[phase]}
    </p>
  );
}

// 작업 취소 버튼. 이미 끝난 단계는 되돌리지 않고, 다음 단계부터 멈춘다.
function CancelButton({ taskId }: { taskId: number }) {
  const cancel = useCancelTask(taskId);
  return (
    <div className="flex flex-col items-start gap-1">
      <Button
        type="button"
        variant="outline"
        className="min-h-11"
        disabled={cancel.isPending}
        onClick={() => cancel.mutate()}
      >
        작업 취소
      </Button>
      {cancel.isError && (
        <p role="alert" className="text-sm text-destructive">
          취소하지 못했습니다. 이미 끝났을 수 있습니다.
        </p>
      )}
    </div>
  );
}
