// 채팅·작업 기록 화면이 쓰는 서버 데이터 훅 모음(TanStack Query).
// 타입은 모두 pnpm gen:api가 만든 schema.d.ts에서 꺼낸다. 손으로 API 타입을 쓰지 않는다.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";

type Schemas = components["schemas"];
export type Conversation = Schemas["ConversationOut"];
export type Message = Schemas["MessageOut"];
export type Task = Schemas["TaskDetail"];
export type TaskSummary = Schemas["TaskSummary"];
export type TaskStatus = Schemas["TaskStatus"];
export type Step = Schemas["StepOut"];

// queryKey는 캐시의 이름표다. 같은 이름표를 쓰는 화면은 한 번 가져온 결과를 함께 쓴다.
// (Django cache.get(key)의 key와 같은 역할. 배열이라 앞부분만 같아도 한꺼번에 무효화할 수 있다)
export const chatKeys = {
  conversations: ["conversations"] as const,
  messages: (conversationId: number) => ["conversations", conversationId, "messages"] as const,
  tasks: ["tasks"] as const,
  task: (taskId: number) => ["tasks", taskId] as const,
};

const FINISHED_STATUSES: readonly TaskStatus[] = ["done", "failed", "cancelled", "budget_exceeded"];

// task가 더 바뀌지 않는 상태인지. waiting_approval은 승인 뒤 이어지므로 끝나지 않은 것으로 본다.
export function isTaskFinished(status: TaskStatus): boolean {
  return FINISHED_STATUSES.includes(status);
}

// 끝나지 않은 task는 1초마다 다시 가져온다. 3단계(즉시 응답 + 폴링)에 대비한 구조이고,
// 지금(1단계)은 전송 응답이 올 때 task가 이미 끝나 있으므로 다시 가져오는 일이 생기지 않는다.
export function taskRefetchInterval(status: TaskStatus | undefined): number | false {
  if (status === undefined || isTaskFinished(status)) return false;
  return 1000;
}

// 메시지 전송 실패를 화면에 보여 줄 한국어 문구로 바꾼다.
export function sendErrorMessage(status: number): string {
  if (status === 422) return "보낼 수 없는 내용입니다. 1~4,000자로 입력해 주세요.";
  if (status === 429) return "너무 자주 보냈습니다. 1분 뒤에 다시 보내 주세요.";
  return `메시지를 보내지 못했습니다 (오류 ${status}). 잠시 후 다시 시도해 주세요.`;
}

// 응답에 data가 없으면(4xx·5xx) 상태 코드를 담은 오류로 바꾼다.
function requireData<T>(data: T | undefined, response: Response, what: string): T {
  if (data === undefined)
    throw new Error(`${what}을(를) 가져오지 못했습니다 (오류 ${response.status})`);
  return data;
}

export function useConversations() {
  return useQuery({
    queryKey: chatKeys.conversations,
    queryFn: async () => {
      const { data, response } = await api.GET("/api/conversations");
      return requireData(data, response, "대화 목록");
    },
  });
}

export function useCreateConversation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async () => {
      const { data, response } = await api.POST("/api/conversations", { body: {} });
      return requireData(data, response, "새 대화");
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: chatKeys.conversations }),
  });
}

export function useMessages(conversationId: number) {
  return useQuery({
    queryKey: chatKeys.messages(conversationId),
    queryFn: async () => {
      const { data, response } = await api.GET("/api/conversations/{conversation_id}/messages", {
        params: { path: { conversation_id: conversationId } },
      });
      return requireData(data, response, "메시지");
    },
  });
}

// 메시지 전송. 1단계 서버는 실행이 끝난 뒤 202 {task_id}로 답하므로, 성공하면 메시지·작업 목록을 다시 가져온다.
export function useSendMessage(conversationId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (content: string) => {
      const result = await api
        .POST("/api/conversations/{conversation_id}/messages", {
          params: { path: { conversation_id: conversationId } },
          body: { content },
        })
        .catch(() => {
          // 네트워크가 끊기면 fetch가 영어 오류를 던지므로 한국어 문구로 바꾼다.
          throw new Error("서버에 연결하지 못했습니다. 네트워크를 확인해 주세요.");
        });
      if (!result.data) throw new Error(sendErrorMessage(result.response.status));
      return result.data;
    },
    // 실패해도 서버에 내 메시지나 실패 답변이 남았을 수 있으므로 성공·실패 모두 다시 가져온다.
    onSettled: async () => {
      await queryClient.invalidateQueries({ queryKey: chatKeys.messages(conversationId) });
      await queryClient.invalidateQueries({ queryKey: chatKeys.tasks });
      await queryClient.invalidateQueries({ queryKey: chatKeys.conversations });
    },
  });
}

export function useTask(taskId: number) {
  return useQuery({
    queryKey: chatKeys.task(taskId),
    queryFn: async () => {
      const { data, response } = await api.GET("/api/tasks/{task_id}", {
        params: { path: { task_id: taskId } },
      });
      return requireData(data, response, "작업");
    },
    // refetchInterval에 함수를 주면 매번 최신 결과를 보고 다시 가져올지 정한다(TanStack Query v5).
    refetchInterval: (query) => taskRefetchInterval(query.state.data?.status),
  });
}

export function useTasks() {
  return useQuery({
    queryKey: chatKeys.tasks,
    queryFn: async () => {
      const { data, response } = await api.GET("/api/tasks", { params: { query: { limit: 50 } } });
      return requireData(data, response, "작업 기록");
    },
  });
}
