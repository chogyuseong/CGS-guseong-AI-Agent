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
