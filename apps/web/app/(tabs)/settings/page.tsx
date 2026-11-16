import { PushSettings } from "@/components/PushSettings";

// 설정 탭. 3단계에서는 알림(웹 푸시) 영역만 있다.
export default function SettingsPage() {
  return (
    <section className="flex flex-1 flex-col gap-3 overflow-y-auto p-4">
      <h1 className="text-lg font-semibold">설정</h1>
      <PushSettings />
    </section>
  );
}
