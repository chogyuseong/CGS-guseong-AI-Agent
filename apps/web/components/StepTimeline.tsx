// 작업 단계(도구 호출) 타임라인. 줄마다 상태 아이콘·문구, 누르면 입력·출력을 펼친다.
// - 쓰는 곳: PendingTask(답변 자리), TaskSteps(답변 아래, 작업 기록 탭). steps는 GET /api/tasks/{id}의 StepOut[].
// - 근거: BUILD_SPEC 10.1 "작업 타임라인"·"상태를 색상만으로 표현하지 않음", 10.6(타임라인에는 실행 메타데이터만),
//   DECISIONS 2026-11-17 화면 절(외부 콘텐츠 상자), 5단계 보강 보관 기간 정리.
// 고칠 때 주의:
// - step.status 값은 서버 StepOut 상태와 글자로 맞물린다. STATUS_ICONS가 Record라 상태가 늘면 tsc가 알려 준다.
//   docs/STATE_MACHINES.md도 같이 본다.
// - e2e(chat.spec.ts, progress.spec.ts)가 단계 버튼 안의 img 이름 "완료"·"진행 중"을, widths·dark-mode .spec.ts가
//   단계 이름(예: "메일 읽는 중")으로 버튼을 찾는다. StepTimeline.test.tsx도 같이 본다.
"use client";

import {
  Ban,
  CircleCheck,
  CircleHelp,
  CircleX,
  Hourglass,
  LoaderCircle,
  type LucideIcon,
  TimerOff,
} from "lucide-react";
import { useState } from "react";
import { ExternalContent } from "@/components/ExternalContent";
import { JobPrepCard, readJobPrep, storedExperienceCount } from "@/components/JobPrepCard";
import { MovieRecommendCard, readMovieRecommend } from "@/components/MovieRecommendCard";
import type { Step } from "@/lib/chat";
import { isMailTool, readExternalOutput } from "@/lib/stepOutput";
import { cn } from "@/lib/utils";

type StatusView = { icon: LucideIcon; name: string; className: string; showName?: boolean };

// 단계 상태별 아이콘과 이름. Record<K, V>는 "K의 모든 값을 키로 갖는 사전" 타입이다.
// (Python의 dict[StepStatus, ...]인데, 상태 하나라도 빠지면 tsc가 오류를 낸다)
// 승인과 관련된 상태(승인 대기, 거절됨, 만료됨)와 결과 불명은 아이콘만으로 구별하기 어려워 줄 끝에 글자도 붙인다(showName).
const STATUS_ICONS: Record<Step["status"], StatusView> = {
  running: {
    icon: LoaderCircle,
    name: "진행 중",
    className: "animate-spin text-muted-foreground",
  },
  done: { icon: CircleCheck, name: "완료", className: "text-success" },
  failed: { icon: CircleX, name: "실패", className: "text-destructive" },
  waiting_approval: {
    icon: Hourglass,
    name: "승인 대기",
    className: "text-warning",
    showName: true,
  },
  rejected: { icon: Ban, name: "거절됨", className: "text-muted-foreground", showName: true },
  expired: { icon: TimerOff, name: "만료됨", className: "text-muted-foreground", showName: true },
  // 1단계 보강: 실행했는지 모른다(실행 도중 서버 중단, 쓰기 요청 시간 초과). 실패와 다르므로 글자로도 알린다.
  unknown: {
    icon: CircleHelp,
    name: "결과 확인 필요",
    className: "text-warning",
    showName: true,
  },
};

// 도구 호출 단계 목록. 각 줄을 누르면 입력과 출력을 펼친다.
// 서버 order 순으로 그린다(받은 배열 순서를 믿지 않는다). label은 서버가 도구 LABELS로 만든 문장이다.
// 막는 사고: 승인 전 단계가 "완료"로 보이는 것. 상태 아이콘은 서버 step.status만 따른다(waiting_approval은 "승인 대기").
export function StepTimeline({ steps }: { steps: Step[] }) {
  if (steps.length === 0) return null;
  // [...steps]로 복사한 뒤 정렬해 원래 배열은 그대로 둔다. (Python의 sorted(steps, key=...))
  const ordered = [...steps].sort((a, b) => a.order - b.order);
  return (
    <ol aria-label="작업 단계" className="mt-2 flex flex-col gap-1">
      {ordered.map((step) => (
        <StepRow key={step.id} step={step} />
      ))}
    </ol>
  );
}

// 결과를 카드로 보여 주는 도구: 단계를 처음부터 펼치고 입력 JSON은 숨긴다(서버 도구 이름과 글자로 맞물린다).
const CARD_TOOLS: ReadonlySet<string> = new Set([
  "compare_products",
  "plan_departure",
  "compare_showtimes",
  "show_job_prep", // 2026-11-18 생활 비서 D: 공고별 준비 카드(JobPrepCard)
  "recommend_movies", // 2026-11-19 영화 비서 C 둘째 단계: 영화 추천 카드(MovieRecommendCard)
]);

// 한 줄. 아이콘에 role=img·aria-label로 상태 이름을 붙여 화면 낭독기와 e2e가 상태를 읽는다.
// showName 글자는 aria-hidden이다(아이콘 이름과 두 번 읽히지 않게).
function StepRow({ step }: { step: Step }) {
  // 2026-11-18 쇼핑 비교는 가격과 판매처 링크를 채팅 답변 아래에 바로 표시한다.
  // 2026-11-18 출발 계산(plan_departure)도 결론·지도 링크를 바로 보이게 펼쳐 둔다.
  const [open, setOpen] = useState(CARD_TOOLS.has(step.tool_name));
  const { icon: Icon, name, className, showName } = STATUS_ICONS[step.status];
  return (
    <li className="rounded-md border bg-background text-sm">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
        className="flex min-h-11 w-full items-center gap-2 px-3 text-left"
      >
        <Icon role="img" aria-label={name} className={cn("size-4 shrink-0", className)} />
        <span className="min-w-0 flex-1 break-words">{step.label}</span>
        {showName && (
          <span aria-hidden="true" className={cn("shrink-0 text-xs", className)}>
            {name}
          </span>
        )}
      </button>
      {open && <StepDetail step={step} />}
    </li>
  );
}

// 외부 콘텐츠 도구의 출력({data: {notice, content, truncated}})은 JSON 덩어리 대신 ExternalContent로,
// 그 밖의 출력은 지금처럼 JSON으로 보여 준다.
// JSON도 <pre> 안의 글자로만 그린다(HTML로 해석하지 않음). 메일 도구 출력은 7단계 보강부터 서버가 내용 없이(통 수만) 저장한다.
function StepDetail({ step }: { step: Step }) {
  const external = readExternalOutput(step.output);
  const jobPrep = step.tool_name === "show_job_prep" ? readJobPrep(step.output) : null;
  const movieRecommend =
    step.tool_name === "recommend_movies" ? readMovieRecommend(step.output) : null;
  const experienceCount =
    step.tool_name === "list_experiences" ? storedExperienceCount(step.output) : null;
  if (isPruned(step)) {
    return (
      <p className="border-t px-3 py-2 text-xs text-muted-foreground">
        보관 기간이 지나 입력과 출력을 지웠습니다. 단계 이름과 결과 상태만 남습니다.
      </p>
    );
  }
  return (
    <div className="flex min-w-0 flex-col gap-2 border-t px-3 py-2 text-xs">
      {!CARD_TOOLS.has(step.tool_name) && <JsonBlock title="입력" value={step.input} />}
      {jobPrep ? (
        <JobPrepCard card={jobPrep} />
      ) : movieRecommend ? (
        <MovieRecommendCard card={movieRecommend} />
      ) : experienceCount !== null ? (
        <p className="text-muted-foreground">
          저장한 경험 {experienceCount}개를 읽었습니다. 작업 기록에는 내용을 남기지 않습니다.
        </p>
      ) : external ? (
        <div className="flex min-w-0 flex-col gap-1">
          <p className="font-medium">출력</p>
          <ExternalContent
            external={external}
            mail={isMailTool(step.tool_name)}
            shopping={step.tool_name === "compare_products"}
            travel={step.tool_name === "plan_departure"}
            movie={step.tool_name === "compare_showtimes"}
          />
        </div>
      ) : (
        <JsonBlock title="출력" value={step.output} />
      )}
    </div>
  );
}

// 5단계 보강: 서버의 보관 기간 정리(app/automation/retention.py)는 끝난 단계의 입력을 {}, 출력을 null로 비운다.
// 고칠 때 주의: 서버가 비우는 모양({}·null)이 바뀌면 이 판정도 바꾼다(docs/DATA_POLICY.md).
function isPruned(step: Step): boolean {
  // 실행을 마친 단계(done·failed·unknown)는 항상 출력이 있다. 거절·만료 단계는 원래 출력이 없어 제외한다.
  const executed = step.status === "done" || step.status === "failed" || step.status === "unknown";
  return executed && step.output === null && Object.keys(step.input ?? {}).length === 0;
}

// unknown: "무엇이든 올 수 있지만 확인 전에는 마음대로 쓰지 못하는 값" 타입. (Python의 object에 가깝다)
function JsonBlock({ title, value }: { title: string; value: unknown }) {
  return (
    <div>
      <p className="font-medium">{title}</p>
      <pre className="overflow-x-auto whitespace-pre-wrap break-all rounded bg-muted p-2">
        {value === null ? "없음" : JSON.stringify(value, null, 2)}
      </pre>
    </div>
  );
}
