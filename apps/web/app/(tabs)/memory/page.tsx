import { MemoryList } from "@/components/MemoryList";

// 기억 탭(5단계). 로그인 확인과 하단 탭은 (tabs)/layout.tsx가 맡는다.
export default function MemoryPage() {
  return (
    <section
      aria-labelledby="memory-title"
      className="flex flex-1 flex-col gap-3 overflow-y-auto p-4"
    >
      <h1 id="memory-title" className="text-lg font-semibold">
        기억
      </h1>
      <MemoryList />
    </section>
  );
}
