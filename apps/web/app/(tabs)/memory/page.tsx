// 맡은 일: 기억 탭(/memory, 5단계). 목록·원래 메시지 보기·한 건 삭제·전체 삭제는 components/MemoryList가 맡는다.
//   여기서는 제목과 스크롤 영역만 둔다.
// 근거: docs/DECISIONS.md 5단계 "화면(apps/web)"(2026-11-17) — 기억을 화면에서 추가·수정하는 기능은 두지 않는다.
// 고칠 때 주의: 경로를 바꾸면 BottomTabs의 TAB_ITEMS와 e2e(memory·dark-mode·widths spec)를 고친다.
//   h1 문구("기억")와 aria-labelledby 연결은 e2e가 쓴다: memory.spec은 region "기억"으로 영역을 찾고,
//   dark-mode·widths spec은 h1 제목으로 화면을 확인한다.
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
