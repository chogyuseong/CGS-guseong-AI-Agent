// 맡은 일: 작업 기록 탭(/tasks). task 목록·상태 배지·단계·승인 카드는 components/TaskList가 그린다.
//   이 탭에서도 대기 중인 승인을 결정할 수 있다(DECISIONS 2단계 "화면(apps/web)" 2026-11-17).
// 폴링: 진행 중 task가 있으면 3초, 승인 대기뿐이면 15초(lib/polling.ts, DECISIONS 9단계 보강 2026-11-18).
// 고칠 때 주의: 경로를 바꾸면 BottomTabs의 TAB_ITEMS와 e2e(dark-mode·widths spec의 page.goto("/tasks"),
//   login.spec의 탭 이름 표)를 고친다.
import { TaskList } from "@/components/TaskList";

// 작업 기록 탭.
export default function TasksPage() {
  return (
    <section className="flex flex-1 flex-col gap-3 overflow-y-auto p-4">
      <h1 className="text-lg font-semibold">작업 기록</h1>
      <TaskList />
    </section>
  );
}
