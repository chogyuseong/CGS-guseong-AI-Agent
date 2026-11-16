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

type ApprovalLike = { status: Schemas["ApprovalStatus"] };

function hasPendingApproval(approvals: readonly ApprovalLike[]): boolean {
  return approvals.some((approval) => approval.status === "pending");
}

// 끝나지 않은 task는 1초마다 다시 가져온다(3단계: 서버가 즉시 202로 답하고 화면이 폴링으로 진행을 본다).
// 승인 대기 중에는 사용자가 결정할 때까지 바뀌지 않으므로 3초로 늦춘다. 다른 기기에서 결정한 결과가
// 이 화면에도 반영되어야 하므로 완전히 멈추지는 않는다.
// 단, 승인을 모두 결정해 서버가 재개를 시작하기 직전(waiting_approval인데 pending이 없음)이면 곧 바뀌므로 1초.
export function taskRefetchInterval(
  status: TaskStatus | undefined,
  approvals?: readonly ApprovalLike[],
): number | false {
  if (status === undefined || isTaskFinished(status)) return false;
  if (status === "waiting_approval") {
    return approvals !== undefined && !hasPendingApproval(approvals) ? 1000 : 3000;
  }
  return 1000;
}

// 답변 자리에 보여 줄 진행 단계.
// starting: 아직 task를 못 가져옴 / resuming: 승인을 모두 결정했고 서버가 재개를 시작하기 전.
export type TaskPhase =
  | "starting"
  | "queued"
  | "running"
  | "waiting_approval"
  | "resuming"
  | "finished";

export const PHASE_TEXT: Record<Exclude<TaskPhase, "finished">, string> = {
  starting: "요청을 보냈습니다. 진행 상황을 확인하는 중…",
  queued: "요청을 받았습니다. 곧 시작합니다…",
  running: "작업하는 중…",
  waiting_approval: "승인이 필요한 작업이 있습니다. 건마다 승인하거나 거절해 주세요.",
  resuming: "결정을 반영해 이어서 처리하는 중…",
};

export function taskPhase(
  task: { status: TaskStatus; approvals: readonly ApprovalLike[] } | undefined,
): TaskPhase {
  if (task === undefined) return "starting";
  if (isTaskFinished(task.status)) return "finished";
  if (task.status === "waiting_approval") {
    return hasPendingApproval(task.approvals) ? "waiting_approval" : "resuming";
  }
  return task.status === "queued" ? "queued" : "running";
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

// 메시지 전송. 3단계 서버는 실행을 기다리지 않고 바로 202 {task_id}로 답한다(그때 task는 보통 queued).
// 그래서 성공하면 메시지 목록을 다시 가져온다. 답변이 없는 내 메시지 아래에 PendingTask가 진행 상황을 폴링한다.
// 전송 요청이 끝나기 전(onSettled가 끝날 때까지)은 "보내는 중" 말풍선이 보이므로 화면이 비지 않는다.
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
    // 화면이 백그라운드(다른 탭, 폰 화면 꺼짐)면 TanStack Query가 폴링을 쉬고, 돌아오면(창 포커스) 바로 다시 가져온다.
    refetchInterval: (query) =>
      taskRefetchInterval(query.state.data?.status, query.state.data?.approvals),
  });
}

// 작업 기록 탭의 목록. 끝나지 않은 task가 있으면 상태 배지가 바뀌도록 3초마다 다시 가져온다.
export function useTasks() {
  return useQuery({
    queryKey: chatKeys.tasks,
    queryFn: async () => {
      const { data, response } = await api.GET("/api/tasks", { params: { query: { limit: 50 } } });
      return requireData(data, response, "작업 기록");
    },
    refetchInterval: (query) =>
      query.state.data?.some((task) => !isTaskFinished(task.status)) ? 3000 : false,
  });
}
