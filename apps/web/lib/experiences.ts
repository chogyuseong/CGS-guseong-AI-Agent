// 설정 화면 '내 경험'이 쓰는 서버 호출(생활 비서 D 첫 단계). 타입은 pnpm gen:api가 만든 schema.d.ts에서 꺼낸다.
//
// API(apps/api/app/routers/experiences.py):
//   GET    /api/experiences       → {experiences: 제목·글자 수(본문 없음), limit, max_body_chars, enabled}
//   GET    /api/experiences/{id}  → 본문까지 / 404 / 암호화 키 없음 503
//   POST   /api/experiences       → {title, body} 등록 / 연락처·주소 400 / 한도 409 / 키 없음 503 / 길이 422
//   DELETE /api/experiences/{id}  → 204 / 404
// 경험은 채팅으로 만들지 않는다(원문이 대화 기록에 평문으로 남기 때문). 2026-11-18 사용자 결정(원문 암호화 + 등록 전 안내).
//
// 쓰는 곳: components/ExperienceList.tsx.
// 고칠 때 주의: 서버 오류 문구(detail 문자열)를 그대로 보여 주는 곳은 400·409·503뿐이다(서버가 값을 되풀이하지 않는
//   한국어 문장을 준다). 테스트: components/ExperienceList.test.tsx, apps/api tests/test_experiences_api.py.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";

export type ExperienceList = components["schemas"]["ExperienceListOut"];
export type ExperienceSummary = components["schemas"]["ExperienceSummaryOut"];
export type Experience = components["schemas"]["ExperienceOut"];

export const experienceKeys = {
  all: ["experiences"] as const,
  one: (id: number) => ["experiences", id] as const,
};

const NETWORK_ERROR = "서버에 연결하지 못했습니다. 네트워크를 확인해 주세요.";
const SAVE_ERROR = "경험을 저장하지 못했습니다. 잠시 후 다시 시도해 주세요.";
const DELETE_ERROR = "경험을 지우지 못했습니다. 잠시 후 다시 시도해 주세요.";

function networkError(): never {
  throw new Error(NETWORK_ERROR);
}

// 서버가 준 한국어 문장(detail 문자열)이면 그대로, 아니면 기본 문구.
function detailOf(error: unknown, fallback: string): string {
  if (typeof error === "object" && error !== null && "detail" in error) {
    const detail = (error as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
  }
  return fallback;
}

async function fetchExperiences(): Promise<ExperienceList> {
  const { data, response } = await api.GET("/api/experiences").catch(networkError);
  if (data === undefined)
    throw new Error(`내 경험을 불러오지 못했습니다 (오류 ${response.status})`);
  return data;
}

async function fetchExperience(id: number): Promise<Experience> {
  const { data, error } = await api
    .GET("/api/experiences/{experience_id}", { params: { path: { experience_id: id } } })
    .catch(networkError);
  if (data === undefined) throw new Error(detailOf(error, "경험을 찾을 수 없습니다."));
  return data;
}

async function addExperience(input: { title: string; body: string }): Promise<ExperienceSummary> {
  const { data, error, response } = await api
    .POST("/api/experiences", { body: input })
    .catch(networkError);
  if (data !== undefined) return data;
  if (response.status === 422) throw new Error("제목이나 본문 길이를 확인해 주세요.");
  throw new Error(detailOf(error, SAVE_ERROR));
}

async function deleteExperience(id: number): Promise<void> {
  const { response } = await api
    .DELETE("/api/experiences/{experience_id}", { params: { path: { experience_id: id } } })
    .catch(networkError);
  if (!response.ok && response.status !== 404) throw new Error(DELETE_ERROR);
}

export function useExperiences() {
  return useQuery({ queryKey: experienceKeys.all, queryFn: fetchExperiences });
}

// 본문 보기. enabled가 false면 부르지 않는다(사용자가 '보기'를 눌렀을 때만 원문을 가져온다).
export function useExperienceBody(id: number, enabled: boolean) {
  return useQuery({
    queryKey: experienceKeys.one(id),
    queryFn: () => fetchExperience(id),
    enabled,
  });
}

export function useAddExperience() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: addExperience,
    onSettled: () => queryClient.invalidateQueries({ queryKey: experienceKeys.all }),
  });
}

export function useDeleteExperience() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: deleteExperience,
    onSettled: () => queryClient.invalidateQueries({ queryKey: experienceKeys.all }),
  });
}
