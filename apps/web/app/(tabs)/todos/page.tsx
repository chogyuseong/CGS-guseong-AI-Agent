// 맡은 일: 할 일 탭(/todos). 체크리스트는 components/TodoChecklist가 맡고, 여기서는 제목과 스크롤 영역만 둔다.
// 근거: 요구사항 밖 기능, 2026-11-18 사용자 결정(docs/DECISIONS.md "요구사항 밖 기능").
// 고칠 때 주의: 경로를 바꾸면 BottomTabs의 TAB_ITEMS와 e2e(login.spec의 탭 이름 표)를 고친다.
import { TodoChecklist } from "@/components/TodoChecklist";

// 할 일 탭. 로그인 확인과 하단 탭은 (tabs)/layout.tsx가 맡는다.
export default function TodosPage() {
  return (
    <section
      aria-labelledby="todos-title"
      className="flex flex-1 flex-col gap-3 overflow-y-auto p-4"
    >
      <h1 id="todos-title" className="text-lg font-semibold">
        할 일
      </h1>
      <TodoChecklist />
    </section>
  );
}
