// 맡은 일: 답변 아래에 "이 답변을 만들 때 함께 보낸 기억"을 접어서 보여 준다(2026-11-19, 분석 38번).
// 서버(GET /api/tasks/{id}의 memories_sent)는 작업을 시작할 때 모델 지시문에 들어간 기억(최근 30개)만 안다.
// 모델이 그중 무엇을 실제로 썼는지는 모르므로 '썼다'가 아니라 '함께 보냈다'고 쓴다. 고치려면 기억 탭으로 간다.
// 쓰는 곳: components/TaskSteps.tsx. 테스트: components/MemoriesSent.test.tsx.
// 고칠 때 주의: 기억 탭 주소(/memory)는 app/(tabs)/memory와 맞물린다. 기억 글은 텍스트로만 그린다.
import Link from "next/link";
import type { Task } from "@/lib/chat";

export function MemoriesSent({ memories }: { memories: NonNullable<Task["memories_sent"]> }) {
  if (memories.length === 0) return null;
  return (
    <details className="mt-2 rounded-md border bg-background text-xs">
      <summary className="flex min-h-11 cursor-pointer items-center px-3">
        {`이 답변을 만들 때 함께 보낸 기억 ${memories.length}개`}
      </summary>
      <div className="flex flex-col gap-1 border-t px-3 py-2">
        <ul className="grid gap-0.5 wrap-anywhere">
          {memories.map((memory) => (
            <li key={memory.id}>{memory.content}</li>
          ))}
        </ul>
        <Link href="/memory" className="text-primary underline">
          기억 탭에서 고치기
        </Link>
      </div>
    </details>
  );
}
