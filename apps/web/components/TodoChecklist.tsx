// 할 일 탭(app/(tabs)/todos/page.tsx)의 체크리스트.
// - 쓰는 lib: lib/todos.ts(GET /api/todos, PUT /api/todos/{id}).
// - 한 줄 전체가 체크 상자의 label이라 어디를 눌러도 체크된다(높이 48px, 모바일에서 장보며 누르기 쉽게).
//   완료한 것은 줄을 긋고 흐리게, 목록 아래쪽에 하루 동안 남는다(잘못 누른 체크를 되돌릴 수 있게).
// 근거: 요구사항 밖 기능, 2026-11-18 사용자 결정(docs/DECISIONS.md "요구사항 밖 기능").
// 고칠 때 주의: 빈 목록 안내 문구와 오류 문구는 components/TodoChecklist.test.tsx가 글자로 찾는다.
//   DONE_HINT의 "하루"는 서버 routers/todos.py DONE_VISIBLE_HOURS(24)와 맞춘다.
"use client";

import { Check } from "lucide-react";
import { useId } from "react";
import { type Todo, useCheckTodo, useTodos } from "@/lib/todos";
import { cn } from "@/lib/utils";

const DONE_HINT = "완료한 할 일은 하루 동안 아래에 남아 있어 다시 눌러 되돌릴 수 있습니다.";
const EMPTY_TEXT =
  '할 일이 없습니다. 채팅에서 "우유 사기 할 일에 추가해 줘"처럼 말하면 여기에 생깁니다.';

export function TodoChecklist() {
  const { data: todos, isPending, isError } = useTodos();
  const check = useCheckTodo();
  if (isPending) return <p className="text-sm text-muted-foreground">불러오는 중…</p>;
  if (isError) return <p className="text-sm text-destructive">할 일 목록을 불러오지 못했습니다.</p>;
  if (todos.length === 0) return <p className="text-sm text-muted-foreground">{EMPTY_TEXT}</p>;
  return (
    <div className="flex flex-col gap-3">
      {check.isError && (
        <p role="alert" className="text-sm text-destructive">
          {check.error.message}
        </p>
      )}
      <ul className="flex flex-col divide-y rounded-lg border">
        {todos.map((todo) => (
          <TodoRow
            key={todo.id}
            todo={todo}
            busy={check.isPending && check.variables?.id === todo.id}
            onToggle={() => check.mutate({ id: todo.id, isDone: !todo.is_done })}
          />
        ))}
      </ul>
      <p className="text-xs text-muted-foreground">{DONE_HINT}</p>
    </div>
  );
}

// 한 줄. 서버에 보내는 동안(busy)은 같은 줄을 다시 누르지 못하게 막는다(두 번 눌러 체크·해제가 엇갈리지 않게).
function TodoRow(props: { todo: Todo; busy: boolean; onToggle: () => void }) {
  const { todo } = props;
  const inputId = useId();
  return (
    <li>
      <label
        htmlFor={inputId}
        className="flex min-h-12 cursor-pointer items-center gap-2 px-1 py-0.5"
      >
        <CheckBox
          id={inputId}
          checked={todo.is_done}
          disabled={props.busy}
          onChange={props.onToggle}
        />
        <span
          className={cn(
            "min-w-0 flex-1 break-words text-base",
            todo.is_done && "text-muted-foreground line-through",
          )}
        >
          {todo.title}
        </span>
      </label>
    </li>
  );
}

// 체크 상자. 실제 input은 44px 터치 영역(모바일 최소 터치 크기, e2e width-audit 기준)으로 두고 모양만 지운다.
// 눈에 보이는 20px 상자와 체크 표시는 옆 span이 peer-checked로 그린다. 키보드 초점은 input에 링으로 보인다.
function CheckBox(props: {
  id: string;
  checked: boolean;
  disabled: boolean;
  onChange: () => void;
}) {
  return (
    <span className="relative grid size-11 shrink-0 place-items-center">
      <input
        id={props.id}
        type="checkbox"
        checked={props.checked}
        disabled={props.disabled}
        onChange={props.onChange}
        className="peer absolute inset-0 size-11 cursor-pointer appearance-none rounded-md focus-visible:outline-2 focus-visible:outline-ring disabled:cursor-wait"
      />
      <span
        aria-hidden="true"
        className="pointer-events-none grid size-5 place-items-center rounded border-2 border-muted-foreground text-primary-foreground peer-checked:border-primary peer-checked:bg-primary [&>svg]:invisible peer-checked:[&>svg]:visible"
      >
        <Check className="size-3.5" strokeWidth={3} />
      </span>
    </span>
  );
}
