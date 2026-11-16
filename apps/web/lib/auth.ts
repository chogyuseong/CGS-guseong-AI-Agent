// 로그인 상태를 다루는 TanStack Query 훅 모음.
// TanStack Query는 서버 데이터를 캐시하는 도구다. (Django의 cache 프레임워크를 화면 쪽에 둔 것과 비슷)
// 같은 queryKey로 부르는 화면들은 한 번 가져온 결과를 함께 쓴다.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";

// 생성된 타입에서 사용자 모양을 꺼낸다. 손으로 { id, email } 타입을 쓰지 않는다.
export type CurrentUser = components["schemas"]["UserOut"];
export type LoginInput = components["schemas"]["LoginRequest"];

export const meQueryKey = ["auth", "me"] as const;

// GET /api/auth/me. 로그인 안 했으면(401) 에러가 아니라 null을 돌려준다.
export async function fetchMe(): Promise<CurrentUser | null> {
  const { data, response } = await api.GET("/api/auth/me");
  if (response.status === 401) return null;
  if (!data) throw new Error(`로그인 상태를 확인하지 못했습니다 (${response.status})`);
  return data;
}

// 훅(use로 시작하는 함수)은 컴포넌트 안에서만 부를 수 있는 React 함수다. (Django 템플릿 태그처럼 정해진 자리에서만 쓰는 도구)
export function useMe() {
  return useQuery({
    queryKey: meQueryKey,
    queryFn: fetchMe,
    retry: false,
  });
}

// POST /api/auth/login. 실패 메시지는 화면에 그대로 보여 줄 한국어 문장으로 만든다.
export async function login(input: LoginInput): Promise<CurrentUser> {
  const { data, response } = await api.POST("/api/auth/login", { body: input });
  if (response.status === 401) throw new Error("이메일이나 비밀번호가 맞지 않습니다.");
  if (response.status === 403)
    throw new Error("허용되지 않은 주소에서 접속했습니다. 서버의 APP_ORIGIN을 확인하세요.");
  if (!data) throw new Error(`로그인하지 못했습니다 (${response.status})`);
  return data;
}

// useMutation은 서버 상태를 바꾸는 요청용이다. (Django 뷰의 POST 처리에 해당)
// 성공하면 /auth/me 캐시를 바로 채워서, 다음 화면이 다시 묻지 않게 한다.
export function useLogin() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: login,
    onSuccess: (user) => {
      queryClient.setQueryData(meQueryKey, user);
    },
  });
}
