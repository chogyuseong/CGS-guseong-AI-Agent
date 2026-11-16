"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import { chatKeys, isTaskFinished, useTask } from "@/lib/chat";
import { TaskSteps } from "./TaskSteps";

// 아직 답변(assistant 메시지)이 없는 task를 답변 자리에 보여 준다.
// 승인 대기 중에는 서버가 답변을 남기지 않으므로 이 자리에 승인 카드가 뜬다.
// task가 끝나면(다른 기기에서 결정한 경우 포함) 메시지 목록을 다시 가져와 최종 답변으로 바꾼다.
export function PendingTask({ taskId }: { taskId: number }) {
  const { data: task } = useTask(taskId);
  const queryClient = useQueryClient();
  const status = task?.status;
  const conversationId = task?.conversation_id;

  useEffect(() => {
    if (status === undefined || conversationId === undefined || !isTaskFinished(status)) return;
    queryClient.invalidateQueries({ queryKey: chatKeys.messages(conversationId) });
  }, [status, conversationId, queryClient]);

  return (
    <>
      {status === "waiting_approval" && (
        <p className="text-sm">승인이 필요한 작업이 있습니다. 건마다 승인하거나 거절해 주세요.</p>
      )}
      {status !== undefined && status !== "waiting_approval" && !isTaskFinished(status) && (
        <p className="text-sm text-muted-foreground">작업하는 중…</p>
      )}
      <TaskSteps taskId={taskId} />
    </>
  );
}
