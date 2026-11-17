// 폴링 간격 규칙(9단계 보강, BUILD_SPEC 10.1 "폴링"). 화면의 refetchInterval은 모두 이 파일의 함수를 쓴다.
// 간격 표와 이유는 docs/DECISIONS.md "9단계 보강(2)"에 있다.
//
// - 실행 중: 1초에서 시작해 같은 단계가 길어질수록 2초 → 3초 → 5초로 늘린다(백오프).
// - 사용자 결정을 기다리는 승인 대기: 15초. 다른 기기에서 결정한 결과도 반영되도록 멈추지는 않는다.
// - 끝난 상태: 멈춘다.
// - 탭이 보이지 않으면(document.visibilityState === "hidden") 멈추고, 다시 보이거나 네트워크가 다시 연결되면
//   바로 서버 상태를 가져온다(TanStack Query의 focusManager·onlineManager. POLLING_QUERY_OPTIONS).
// chat.ts도 이 파일을 불러오지만(useTask), 서로 함수 정의만 가져오고 불러올 때 실행하지는 않아 순환해도 안전하다.
import { isTaskFinished, type TaskPhase, type TaskStatus } from "@/lib/chat";

export const APPROVAL_POLL_MS = 15_000;
export const TASK_LIST_ACTIVE_POLL_MS = 3_000;

// [이 시간(ms)이 지나면, 이 간격(ms)으로]. 앞에서부터 차례로 본다.
const BACKOFF_STEPS: readonly (readonly [number, number])[] = [
  [60_000, 5_000],
  [30_000, 3_000],
  [10_000, 2_000],
  [0, 1_000],
];

export function backoffInterval(elapsedMs: number): number {
  const elapsed = Math.max(0, elapsedMs);
  const found = BACKOFF_STEPS.find(([after]) => elapsed >= after);
  return found ? found[1] : 1_000;
}

// task 한 건(진행 표시, 작업 기록의 단계 보기). elapsedMs는 지금 단계를 처음 본 뒤 지난 시간이다.
export function taskPollInterval(phase: TaskPhase, elapsedMs: number): number | false {
  if (phase === "finished" || phase === "starting") return false;
  if (phase === "waiting_approval") return APPROVAL_POLL_MS;
  return backoffInterval(elapsedMs);
}

// 작업 기록 목록. 목록 한 줄에는 승인 내용이 없어 승인 대기는 모두 사용자 결정 대기로 본다.
export function taskListPollInterval(statuses: readonly TaskStatus[] | undefined): number | false {
  const open = (statuses ?? []).filter((status) => !isTaskFinished(status));
  if (open.length === 0) return false;
  return open.every((status) => status === "waiting_approval")
    ? APPROVAL_POLL_MS
    : TASK_LIST_ACTIVE_POLL_MS;
}

// 백오프의 기준 시각. 서버 시각(created_at)이 아니라 이 화면이 지금 단계를 처음 본 시각으로 잰다
// (기기 시계가 어긋나도, 승인 뒤 재개처럼 단계가 바뀌어도 다시 1초부터 시작하도록).
export function createPhaseClock() {
  let current: { phase: string; since: number } | undefined;
  return {
    elapsed(phase: string, now: number): number {
      if (current?.phase !== phase) current = { phase, since: now };
      return now - current.since;
    },
  };
}

// 폴링하는 쿼리에 함께 넣는 옵션. 숨은 탭에서는 refetchInterval을 쉬고,
// 다시 보이거나(visibilitychange) 다시 연결되면 캐시가 새것이어도 서버 상태를 가져온다.
export const POLLING_QUERY_OPTIONS = {
  refetchIntervalInBackground: false,
  refetchOnWindowFocus: "always",
  refetchOnReconnect: "always",
} as const;
