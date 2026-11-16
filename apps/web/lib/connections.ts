// 외부 서비스 연결(4단계: Google) 화면이 쓰는 서버 호출과 문구. 타입은 pnpm gen:api가 만든 schema.d.ts에서 꺼낸다.
//
// 연결 흐름(apps/api/app/routers/connections.py):
//   [Google 계정 연결] 링크 → GET /api/connections/google/start(링크 이동, fetch 아님) → Google 동의 화면
//   → 콜백 → /connections?google=connected 또는 /connections?google=error&reason=<코드>로 돌아온다.
//   [메일 읽기 권한 추가] 링크 → GET /api/connections/google/start?scope=mail → 성공하면 ?google=connected&added=mail.
//   메일 권한만 끄는 방법은 없다(Google이 범위 일부 철회를 지원하지 않음). 빼려면 연결 해제 후 캘린더만 다시 연결한다.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";

export type Connection = components["schemas"]["ConnectionOut"];

export const CONNECTIONS_PATH = "/connections";
export const GOOGLE_START_URL = "/api/connections/google/start";
// 7단계 '메일 읽기 권한 추가': 같은 start 경로에 scope=mail. 서버가 캘린더 + gmail.readonly를 함께 요청한다
// (연결 전에 눌러도 캘린더와 메일이 함께 연결된다). 기본 연결(GOOGLE_START_URL)은 캘린더만 요청한다.
export const GOOGLE_MAIL_START_URL = `${GOOGLE_START_URL}?scope=mail`;
export const connectionKeys = { all: ["connections"] as const };

export type Notice = { tone: "success" | "error"; text: string };

const CONNECTED_TEXT =
  "Google 계정을 연결했습니다. 이제 채팅에서 캘린더 일정을 볼 수 있고, 승인을 거쳐 일정을 만들고 고칠 수 있습니다.";
const MAIL_ADDED_TEXT =
  "메일 읽기 권한을 추가했습니다. 이제 채팅에서 메일을 찾아 읽을 수 있습니다(읽기만 하고 보내거나 지우지 않습니다).";
const UNKNOWN_ERROR_TEXT = "Google 계정을 연결하지 못했습니다. 다시 시도해 주세요.";

// 콜백 오류 코드(서버가 정한 9가지)별 안내. 주소의 글자는 화면에 옮기지 않고 아는 코드만 문장으로 바꾼다.
export const CALLBACK_REASONS: Record<string, string> = {
  not_logged_in: "로그인이 풀려 연결을 마치지 못했습니다. 다시 로그인한 뒤 연결해 주세요.",
  access_denied: "Google 동의 화면에서 허용하지 않아 연결하지 않았습니다.",
  google_error: "Google에서 연결을 거절했습니다. 잠시 후 다시 시도해 주세요.",
  invalid_state:
    "연결 요청이 만료되었거나 올바르지 않습니다(10분 안에 마쳐야 합니다). 이 화면에서 다시 연결해 주세요.",
  invalid_request: "Google에서 돌아온 응답이 올바르지 않습니다. 다시 연결해 주세요.",
  exchange_failed: "Google에서 연결 정보를 받아 오지 못했습니다. 잠시 후 다시 시도해 주세요.",
  scope_missing:
    "캘린더 권한을 허용하지 않아 연결하지 않았습니다. 다시 연결할 때 캘린더 일정 항목에 체크해 주세요.",
  not_configured:
    "서버에 Google 연결 설정이 없어 연결할 수 없습니다. 서버의 .env 설정을 확인해 주세요.",
  // 7단계: 메일 권한 추가에서 메일 항목만 빼고 허용함. 서버는 캘린더 연결을 저장하고 이 코드로 돌려보낸다.
  mail_scope_missing:
    "메일 읽기 권한을 허용하지 않아 메일 권한은 추가하지 않았습니다. 캘린더 연결은 그대로 저장했습니다. 메일을 읽게 하려면 '메일 읽기 권한 추가'를 다시 누르고 Gmail 항목에 체크해 주세요.",
};

type ParamReader = { get(name: string): string | null; has(name: string): boolean };

export function callbackNotice(params: ParamReader): Notice | null {
  const result = params.get("google");
  if (result === "connected") {
    const mailAdded = params.get("added") === "mail";
    return { tone: "success", text: mailAdded ? MAIL_ADDED_TEXT : CONNECTED_TEXT };
  }
  if (result !== "error") return null;
  const reason = params.get("reason") ?? "";
  const text = Object.hasOwn(CALLBACK_REASONS, reason) ? CALLBACK_REASONS[reason] : undefined;
  return { tone: "error", text: text ?? UNKNOWN_ERROR_TEXT };
}

// 돌아온 주소의 쿼리(google, reason, added)를 지워야 하는지. 새로 고침하면 같은 안내가 다시 뜨지 않게 한다.
export function hasCallbackParams(params: ParamReader): boolean {
  return params.has("google") || params.has("reason") || params.has("added");
}

export function formatConnectedDate(value: string): string {
  return new Date(value).toLocaleDateString("ko-KR", {
    timeZone: "Asia/Seoul",
    year: "numeric",
    month: "long",
    day: "numeric",
  });
}

export function useConnections() {
  return useQuery({
    queryKey: connectionKeys.all,
    queryFn: async () => {
      const { data, response } = await api.GET("/api/connections");
      if (data === undefined)
        throw new Error(`연결 상태를 가져오지 못했습니다 (오류 ${response.status})`);
      return data;
    },
  });
}

const DISCONNECT_ERROR = "연결을 해제하지 못했습니다. 잠시 후 다시 시도해 주세요.";

async function disconnectGoogle(): Promise<void> {
  const result = await api
    .DELETE("/api/connections/{provider}", { params: { path: { provider: "google" } } })
    .catch(() => {
      // 네트워크가 끊기면 fetch가 영어 오류를 던지므로 한국어 문구로 바꾼다.
      throw new Error("서버에 연결하지 못했습니다. 네트워크를 확인해 주세요.");
    });
  if (!result.response.ok) throw new Error(DISCONNECT_ERROR);
}

// 연결 해제. 성공·실패 모두 연결 상태를 다시 가져온다(실패해도 서버에서는 지워졌을 수 있다).
export function useDisconnectGoogle() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: disconnectGoogle,
    onSettled: () => queryClient.invalidateQueries({ queryKey: connectionKeys.all }),
  });
}
