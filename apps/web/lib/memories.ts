// 기억 화면(5단계)이 쓰는 서버 호출과 문구. 타입은 pnpm gen:api가 만든 schema.d.ts에서 꺼낸다.
//
// API(apps/api/app/routers/memories.py):
//   GET    /api/memories              → {memories: 최근 것부터, limit: 200}. 각 기억에 원래 메시지(source)가 붙어 있다.
//   DELETE /api/memories/{memory_id}  → 204 / 없거나 남의 것이면 404
//   DELETE /api/memories              → 200 {deleted}
// 기억을 만들거나 고치는 API는 없다. 기억은 채팅에서 "…기억해"라고 말할 때만 생긴다.
//
// 쓰는 곳: components/MemoryList.tsx(기억 탭 전체), components/AutomationItems.tsx(conversationHref).
// 근거: BUILD_SPEC 절대 원칙 9(기억은 명시적 요청과 실제 출처에서만), DECISIONS 2026-11-17 "기억 API (화면용)"·
// 5단계 "화면(apps/web)" 절(목록·원래 메시지·한 건 삭제·전체 삭제만, 전체 삭제 확인은 화면 몫).
//
// 고칠 때 주의:
// - 최대 개수(200)는 응답의 limit을 쓴다. 숫자를 화면에 박지 않는다.
// - memoryKeys.all은 lib/chat.ts의 대화 삭제 후 무효화(["memories"])와 글자로 맞물린다.
// - conversationHref는 app/(tabs)/chat/[id] 경로 모양에 기댄다. 채팅 경로를 바꾸면 여기도 바꾼다.
// - 테스트: lib/memories.test.ts, components/MemoryList.test.tsx, e2e/memory.spec.ts.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";

export type Memory = components["schemas"]["MemoryOut"];

export const memoryKeys = { all: ["memories"] as const };

const NETWORK_ERROR = "서버에 연결하지 못했습니다. 네트워크를 확인해 주세요.";
const NOT_FOUND_ERROR = "이미 지워졌거나 찾을 수 없는 기억입니다. 목록을 다시 불러왔습니다.";
const DELETE_ERROR = "기억을 지우지 못했습니다. 잠시 후 다시 시도해 주세요.";
const DELETE_ALL_ERROR =
  "기억을 모두 지우지 못했습니다. 일부는 지워졌을 수 있어 목록을 다시 불러왔습니다.";

export function formatMemoryDate(value: string): string {
  return new Date(value).toLocaleDateString("ko-KR", {
    timeZone: "Asia/Seoul",
    year: "numeric",
    month: "long",
    day: "numeric",
  });
}

export function formatMessageTime(value: string): string {
  return new Date(value).toLocaleString("ko-KR", {
    timeZone: "Asia/Seoul",
    year: "numeric",
    month: "long",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

export function conversationHref(conversationId: number): string {
  return `/chat/${conversationId}`;
}

export function memoryCountText(count: number, limit: number): string {
  return `저장된 기억 ${count}개 / 최대 ${limit}개`;
}

// 네트워크가 끊기면 fetch가 영어 오류를 던지므로 한국어 문구로 바꾼다.
function networkError(): never {
  throw new Error(NETWORK_ERROR);
}

async function fetchMemories() {
  const { data, response } = await api.GET("/api/memories").catch(networkError);
  if (data === undefined)
    throw new Error(`기억 목록을 불러오지 못했습니다 (오류 ${response.status})`);
  return data;
}

async function deleteMemory(memoryId: number): Promise<void> {
  const { response } = await api
    .DELETE("/api/memories/{memory_id}", { params: { path: { memory_id: memoryId } } })
    .catch(networkError);
  if (response.status === 404) throw new Error(NOT_FOUND_ERROR);
  if (!response.ok) throw new Error(DELETE_ERROR);
}

// 지운 개수를 돌려준다. 확인 인자는 서버가 요구하지 않으므로, 한 번 더 묻는 확인 영역(MemoryList)을 건너뛰고
// 부르면 바로 모두 지워진다(되돌릴 수 없음).
async function deleteAllMemories(): Promise<number> {
  const { data, response } = await api.DELETE("/api/memories").catch(networkError);
  if (!response.ok || data === undefined) throw new Error(DELETE_ALL_ERROR);
  return data.deleted;
}

export function useMemories() {
  return useQuery({ queryKey: memoryKeys.all, queryFn: fetchMemories });
}

// 삭제는 성공·실패 모두 목록을 다시 가져온다(실패해도 서버에서는 지워졌을 수 있다. 404면 이미 없는 것).
export function useDeleteMemory() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: deleteMemory,
    onSettled: () => queryClient.invalidateQueries({ queryKey: memoryKeys.all }),
  });
}

export function useDeleteAllMemories() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: deleteAllMemories,
    onSettled: () => queryClient.invalidateQueries({ queryKey: memoryKeys.all }),
  });
}
