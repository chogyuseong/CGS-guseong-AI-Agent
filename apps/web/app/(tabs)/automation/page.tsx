// 맡은 일: 자동화 탭(/automation, 6단계). 예약 작업·조건 감시 목록과 켜기/끄기·삭제·마지막 실행 결과는
//   components/AutomationList가 그린다. 여기서는 제목과 스크롤 영역만 둔다.
// 근거: docs/DECISIONS.md 6단계 "화면(apps/web)"(2026-11-17) — 예약·감시는 화면에서 만들지 않고 채팅으로만 만든다.
// 고칠 때 주의: 경로를 바꾸면 BottomTabs의 TAB_ITEMS와 e2e(automation·dark-mode·widths spec)를 고친다.
//   h1 문구("자동화")와 aria-labelledby 연결은 e2e가 쓴다: automation.spec은 region "자동화"로 영역을 찾고,
//   dark-mode·widths spec은 h1 제목으로 화면을 확인한다.
import { AutomationList } from "@/components/AutomationList";

// 자동화 탭(6단계). 로그인 확인과 하단 탭은 (tabs)/layout.tsx가 맡는다.
// aria-labelledby: 화면 읽기 프로그램이 이 영역을 h1 제목("자동화")으로 부른다.
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
