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
  done: { icon: CircleCheck, name: "완료", className: "text-green-600" },
  failed: { icon: CircleX, name: "실패", className: "text-destructive" },
  waiting_approval: {
    icon: Hourglass,
    name: "승인 대기",
    className: "text-amber-600",
    showName: true,
  },
  rejected: { icon: Ban, name: "거절됨", className: "text-muted-foreground", showName: true },
  expired: { icon: TimerOff, name: "만료됨", className: "text-muted-foreground", showName: true },
  // 1단계 보강: 실행했는지 모른다(실행 도중 서버 중단, 쓰기 요청 시간 초과). 실패와 다르므로 글자로도 알린다.
  unknown: {
    icon: CircleHelp,
    name: "결과 확인 필요",
    className: "text-amber-600",
    showName: true,
  },
};

// 도구 호출 단계 목록. 각 줄을 누르면 입력과 출력을 펼친다.
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

function StepRow({ step }: { step: Step }) {
  const [open, setOpen] = useState(false);
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
function StepDetail({ step }: { step: Step }) {
  const external = readExternalOutput(step.output);
  if (isPruned(step)) {
    return (
      <p className="border-t px-3 py-2 text-xs text-muted-foreground">
        보관 기간이 지나 입력과 출력을 지웠습니다. 단계 이름과 결과 상태만 남습니다.
      </p>
    );
  }
  return (
    <div className="flex min-w-0 flex-col gap-2 border-t px-3 py-2 text-xs">
      <JsonBlock title="입력" value={step.input} />
      {external ? (
        <div className="flex min-w-0 flex-col gap-1">
          <p className="font-medium">출력</p>
          <ExternalContent external={external} mail={isMailTool(step.tool_name)} />
        </div>
      ) : (
        <JsonBlock title="출력" value={step.output} />
      )}
    </div>
  );
}

// 5단계 보강: 서버의 보관 기간 정리(app/automation/retention.py)는 끝난 단계의 입력을 {}, 출력을 null로 비운다.
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
