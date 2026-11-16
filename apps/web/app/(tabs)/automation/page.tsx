import { AutomationList } from "@/components/AutomationList";

// 자동화 탭(6단계). 로그인 확인과 하단 탭은 (tabs)/layout.tsx가 맡는다.
export default function AutomationPage() {
  return (
    <section
      aria-labelledby="automation-title"
      className="flex flex-1 flex-col gap-3 overflow-y-auto p-4"
    >
      <h1 id="automation-title" className="text-lg font-semibold">
        자동화
      </h1>
      <AutomationList />
    </section>
  );
}
