// task 하나의 승인 카드 목록과 카드별 결정 연결.
// - 쓰는 곳: PendingTask(답변 자리), TaskSteps(답변 아래, 작업 기록 탭). 같은 승인이 여러 화면에 보여도
//   결정은 서버에서 한 번만 받아들여지고(FOR UPDATE), 나머지 화면은 task 폴링으로 최신 상태를 받는다.
// - 쓰는 lib: lib/approvals.ts useDecideApproval(POST /api/approvals/{approval_id}/approve·reject, 성공하면
//   task 캐시에 applyDecision으로 바로 반영, 끝나면 task·메시지·작업 기록 캐시 무효화).
// - 근거: DECISIONS 2026-11-17 화면 절(모든 승인을 step 순서로, 결정된 카드는 결과만), 절대 원칙 3.
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
// 카드에는 결정 뒤 승인 상태만 돌려준다. 실패는 ApprovalActionError(code로 분기)를 그대로 던져 카드가 처리한다.
function ApprovalItem({ task, approval }: { task: Task; approval: Approval }) {
  const decide = useDecideApproval(task, approval.id);
  return (
    <ApprovalCard
      approval={approval}
      onDecide={async (decision) => (await decide.mutateAsync(decision)).approval.status}
    />
  );
}
