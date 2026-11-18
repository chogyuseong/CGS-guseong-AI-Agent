// 할 일 탭(체크리스트)이 쓰는 서버 호출. 타입은 pnpm gen:api가 만든 schema.d.ts에서 꺼낸다.
//
// API(apps/api/app/routers/todos.py):
//   GET /api/todos            → {todos: 안 한 것(오래된 것부터) + 최근 24시간 안에 완료한 것(최근 것부터)}
//   PUT /api/todos/{todo_id}  → {"is_done": true|false}로 체크·체크 해제, 바뀐 할 일 / 없거나 남의 것이면 404
// 할 일을 만드는 API는 없다. 할 일은 채팅("…할 일에 추가해 줘", add_todo 도구)으로만 생긴다.
//
// 쓰는 곳: components/TodoChecklist.tsx.
// 근거: 요구사항 밖 기능, 2026-11-18 사용자 결정(docs/DECISIONS.md "요구사항 밖 기능"). 장을 보며 산 것만
//   누르는 쓰임새라 채팅·모델 호출 없이 바로 바꾼다.
//
// 고칠 때 주의:
// - todoKeys.all은 다른 곳에서 무효화하지 않는다. 할 일 탭을 열 때마다 새로 가져온다(QueryClient 기본 staleTime 0).
// - 테스트: components/TodoChecklist.test.tsx, apps/api tests/test_todos_api.py.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";

export type Todo = components["schemas"]["TodoOut"];

export const todoKeys = { all: ["todos"] as const };

const NETWORK_ERROR = "서버에 연결하지 못했습니다. 네트워크를 확인해 주세요.";
const NOT_FOUND_ERROR = "이미 지워졌거나 찾을 수 없는 할 일입니다. 목록을 다시 불러왔습니다.";
const CHECK_ERROR = "할 일을 바꾸지 못했습니다. 잠시 후 다시 시도해 주세요.";

// 네트워크가 끊기면 fetch가 영어 오류를 던지므로 한국어 문구로 바꾼다.
function networkError(): never {
  throw new Error(NETWORK_ERROR);
}

async function fetchTodos(): Promise<Todo[]> {
  const { data, response } = await api.GET("/api/todos").catch(networkError);
  if (data === undefined)
    throw new Error(`할 일 목록을 불러오지 못했습니다 (오류 ${response.status})`);
  return data.todos;
}

async function checkTodo(input: { id: number; isDone: boolean }): Promise<Todo> {
  const { data, response } = await api
    .PUT("/api/todos/{todo_id}", {
      params: { path: { todo_id: input.id } },
      body: { is_done: input.isDone },
    })
    .catch(networkError);
  if (response.status === 404) throw new Error(NOT_FOUND_ERROR);
  if (data === undefined) throw new Error(CHECK_ERROR);
  return data;
}

export function useTodos() {
  return useQuery({ queryKey: todoKeys.all, queryFn: fetchTodos });
}

// 체크·체크 해제. 성공하면 받은 할 일로 목록의 그 줄만 바로 바꾼다(다시 가져오기 전에도 체크가 보이도록).
// 성공·실패 모두 끝나면 목록을 다시 가져온다(완료한 것이 아래로 내려가는 순서는 서버가 정한다).
export function useCheckTodo() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: checkTodo,
    onSuccess: (changed) =>
      queryClient.setQueryData<Todo[]>(todoKeys.all, (todos) =>
        todos?.map((todo) => (todo.id === changed.id ? changed : todo)),
      ),
    onSettled: () => queryClient.invalidateQueries({ queryKey: todoKeys.all }),
  });
}
