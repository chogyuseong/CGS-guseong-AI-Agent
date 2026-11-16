"use client";

import { type Approval, useDecideApproval } from "@/lib/approvals";
import type { Task } from "@/lib/chat";
import { ApprovalCard } from "./ApprovalCard";

// task의 승인 카드 목록(대기·승인·거절·만료 모두, 서버가 step 순서로 준다).
// 대기 중인 카드에만 버튼이 있고, 결정된 카드는 결과만 남는다. 건마다 따로 결정한다.
export function TaskApprovals({ task }: { task: Task }) {
  if (task.approvals.length === 0) return null;
  return (
    <div className="mt-2 flex flex-col gap-2">
      {task.approvals.map((approval) => (
        <ApprovalItem key={approval.id} task={task} approval={approval} />
      ))}
    </div>
  );
}

// 카드마다 서버 호출(mutation)을 따로 둔다. 한 카드의 진행·오류가 다른 카드에 섞이지 않는다.
function ApprovalItem({ task, approval }: { task: Task; approval: Approval }) {
  const decide = useDecideApproval(task, approval.id);
  return (
    <ApprovalCard
      approval={approval}
      onDecide={async (decision) => (await decide.mutateAsync(decision)).approval.status}
    />
  );
}
