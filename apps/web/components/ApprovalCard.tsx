"use client";

import { LoaderCircle, ShieldCheck, ShieldQuestionMark, ShieldX } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  type Approval,
  ApprovalActionError,
  type ApprovalStatus,
  type Decision,
  executionText,
  formatExpiryTime,
  formatRemaining,
  MISMATCH_CODE,
} from "@/lib/approvals";
import { fieldLabel, formatFieldValue, toolDisplayName } from "@/lib/toolText";

type ApprovalCardProps = {
  approval: Approval;
  // 서버에 결정을 보내고 결정 뒤 승인 상태를 돌려준다. 실패하면 ApprovalActionError를 던진다.
  onDecide: (decision: Decision) => Promise<ApprovalStatus>;
};

const DECIDED_LABELS: Record<Exclude<ApprovalStatus, "pending">, string> = {
  approved: "승인함",
  rejected: "거절함",
  expired: "만료됨",
  cancelled: "작업 취소로 닫힘",
};

// 승인 카드 하나. 외부에 흔적을 남기는 도구 호출 한 건을 실행 전에 보여 주고 사용자가 승인·거절한다.
// 여러 건이면 카드도 여러 장이고 건마다 따로 결정한다("모두 승인"은 두지 않는다: 명세의 절대 원칙).
export function ApprovalCard({ approval, onDecide }: ApprovalCardProps) {
  const flow = useDecisionFlow(onDecide);
  // 서버 데이터가 아직 pending이어도 방금 받은 결정 결과가 있으면 그것을 먼저 보여 준다.
  const status = approval.status === "pending" ? (flow.decided ?? "pending") : approval.status;
  return (
    <article
      aria-label="승인 요청"
      className="flex min-w-0 flex-col gap-3 rounded-xl border bg-card p-3 text-card-foreground shadow-sm"
    >
      <CardHeader status={status} />
      {/* 2단계 보강: 날짜·대상·계정·변경 전후를 감시 배지보다 먼저, 더 크게 보여 준다(BUILD_SPEC 3.6). */}
      <TargetLines target={approval.target} account={approval.account} />
      {/* summary에는 「」로 감싼 외부 문자열(일정 제목)이 섞여 올 수 있다. 텍스트로만 그리고 긴 글자는 어디서든 줄을 바꾼다. */}
      <p className="text-base font-medium wrap-anywhere">{approval.summary}</p>
      <GuardVerdictView guard={approval.guard} />
      <ToolInputDetails toolName={approval.tool_name} input={approval.tool_input} />
      {status === "pending" && <ExpiryLine expiresAt={approval.expires_at} />}
      <ExecutionLine approval={approval} />
      {status === "pending" && <DecisionArea approval={approval} flow={flow} />}
      {flow.error && (
        <p role="alert" className="text-sm text-destructive">
          {flow.error}
        </p>
      )}
    </article>
  );
}

type DecisionFlow = ReturnType<typeof useDecisionFlow>;

// 버튼을 누른 순간부터 응답이 올 때까지 잠근다. 상태(state)는 다음 그리기에서야 바뀌므로,
// 같은 순간에 두 번 눌리는 경우를 ref(즉시 바뀌는 값)로 한 번 더 막는다.
function useDecisionFlow(onDecide: ApprovalCardProps["onDecide"]) {
  const [busy, setBusy] = useState<"approve" | "reject" | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [decided, setDecided] = useState<ApprovalStatus | null>(null);
  const locked = useRef(false);

  async function submit(decision: Decision) {
    if (locked.current) return;
    locked.current = true;
    setBusy(decision.approve ? "approve" : "reject");
    setError(null);
    try {
      const status = await onDecide(decision);
      setDecided(status);
      // 결정됐으면 카드에서 버튼이 사라지므로 잠금을 풀지 않는다.
      if (status === "pending") unlock();
    } catch (caught) {
      unlock();
      if (caught instanceof ApprovalActionError && caught.code === MISMATCH_CODE) {
        setConfirming(true);
        return;
      }
      setError(caught instanceof Error ? caught.message : "승인을 처리하지 못했습니다.");
    }
  }

  function unlock() {
    locked.current = false;
    setBusy(null);
  }

  return { busy, confirming, setConfirming, error, decided, submit };
}

function DecisionArea({ approval, flow }: { approval: Approval; flow: DecisionFlow }) {
  const disabled = flow.busy !== null;
  const approve = () => {
    // mismatch면 바로 보내지 않고 카드 안에서 한 번 더 묻는다.
    if (approval.needs_mismatch_confirmation) flow.setConfirming(true);
    else flow.submit({ approve: true, confirmMismatch: false });
  };
  const reject = () => flow.submit({ approve: false, confirmMismatch: false });
  return (
    <div className="flex flex-col gap-2">
      {flow.confirming ? (
        <MismatchConfirm flow={flow} disabled={disabled} />
      ) : (
        <div className="grid grid-cols-2 gap-2">
          <ActionButton label="승인" onClick={approve} disabled={disabled} primary />
          <ActionButton label="거절" onClick={reject} disabled={disabled} />
        </div>
      )}
      {flow.busy && <BusyNotice action={flow.busy} />}
    </div>
  );
}

function MismatchConfirm({ flow, disabled }: { flow: DecisionFlow; disabled: boolean }) {
  const boxRef = useRef<HTMLDivElement>(null);
  // 폰에서는 이 확인 영역이 화면 아래로 가려질 수 있어 열릴 때 보이는 곳까지 스크롤한다.
  // (jsdom 테스트에는 scrollIntoView가 없어서 ?.()로 부른다)
  useEffect(() => {
    boxRef.current?.scrollIntoView?.({ block: "nearest" });
  }, []);
  return (
    <div
      ref={boxRef}
      className="flex flex-col gap-2 rounded-lg border border-destructive/40 bg-destructive/5 p-2"
    >
      <p className="text-sm font-medium text-destructive">
        요청과 다를 수 있습니다. 그래도 진행할까요?
      </p>
      <div className="grid grid-cols-2 gap-2">
        <ActionButton
          label="그래도 승인"
          onClick={() => flow.submit({ approve: true, confirmMismatch: true })}
          disabled={disabled}
          primary
        />
        <ActionButton label="취소" onClick={() => flow.setConfirming(false)} disabled={disabled} />
      </div>
    </div>
  );
}

type ActionButtonProps = {
  label: string;
  onClick: () => void;
  disabled: boolean;
  primary?: boolean;
};

// 손가락으로 누르기 쉽게 높이 48px, 칸 너비 전체(명세: 승인·거절 버튼 높이 48px, 폭 전체).
function ActionButton({ label, onClick, disabled, primary = false }: ActionButtonProps) {
  return (
    <Button
      type="button"
      variant={primary ? "default" : "outline"}
      onClick={onClick}
      disabled={disabled}
      className="h-12 w-full min-w-0 text-base"
    >
      {label}
    </Button>
  );
}

function BusyNotice({ action }: { action: "approve" | "reject" }) {
  return (
    <p role="status" className="flex items-center gap-2 text-sm text-muted-foreground">
      <LoaderCircle aria-hidden="true" className="size-4 shrink-0 animate-spin" />
      {action === "approve" ? "승인 처리 중… 작업을 이어서 실행하고 있습니다." : "거절 처리 중…"}
    </p>
  );
}

function CardHeader({ status }: { status: ApprovalStatus }) {
  if (status === "pending") {
    return <p className="text-sm font-semibold">승인이 필요합니다</p>;
  }
  return (
    <Badge variant={status === "approved" ? "default" : "secondary"} className="h-6 text-sm">
      {DECIDED_LABELS[status]}
    </Badge>
  );
}

// 감시 에이전트 판정 배지(BUILD_SPEC 3.6 문구): match=초록 'AI 검토: 일치로 추정', mismatch=빨강 'AI 검토: 요청과 다를 수 있음'과
// 이유, unsure=회색 'AI 검토: 확인 필요'. AI의 추정이므로 날짜·대상 줄보다 작게 둔다.
function GuardVerdictView({ guard }: { guard: Approval["guard"] }) {
  if (guard === null) {
    return <Badge variant="secondary">감시 결과 없음</Badge>;
  }
  const mismatch = guard.verdict === "mismatch";
  return (
    <div className="flex flex-col gap-1">
      <GuardBadge verdict={guard.verdict} />
      <p className={mismatch ? "text-sm text-destructive" : "text-sm text-muted-foreground"}>
        {guard.reason}
      </p>
    </div>
  );
}

function GuardBadge({ verdict }: { verdict: NonNullable<Approval["guard"]>["verdict"] }) {
  if (verdict === "match") {
    return (
      <Badge className="bg-green-600/10 text-green-700 dark:text-green-400">
        <ShieldCheck aria-hidden="true" />
        AI 검토: 일치로 추정
      </Badge>
    );
  }
  if (verdict === "mismatch") {
    return (
      <Badge variant="destructive">
        <ShieldX aria-hidden="true" />
        AI 검토: 요청과 다를 수 있음
      </Badge>
    );
  }
  return (
    <Badge variant="secondary">
      <ShieldQuestionMark aria-hidden="true" />
      AI 검토: 확인 필요
    </Badge>
  );
}

// 도구 입력값은 접어 둔다(명세: 입력값 접기). 펼치면 항목마다 한 줄.
// 칸 이름은 한국어로(title → 제목), 시각은 "2026년 11월 20일(금) 19:00"처럼 바꿔 보여 준다(lib/toolText.ts).
// 값은 모두 React 텍스트로만 그린다(HTML로 해석하지 않음). 메모의 줄바꿈은 그대로 보인다.
function ToolInputDetails({
  toolName,
  input,
}: {
  toolName: string;
  input: Approval["tool_input"];
}) {
  return (
    <details className="rounded-md border text-sm">
      <summary className="flex min-h-11 cursor-pointer items-center px-3">입력값 보기</summary>
      <dl className="flex flex-col gap-1 border-t px-3 py-2 text-xs">
        <InputRow name="도구" text={toolDisplayName(toolName)} />
        {Object.entries(input).map(([key, value]) => (
          <InputRow key={key} name={fieldLabel(key)} text={formatFieldValue(value)} />
        ))}
      </dl>
    </details>
  );
}

function InputRow({ name, text }: { name: string; text: string }) {
  return (
    <div className="flex min-w-0 gap-2">
      <dt className="shrink-0 font-medium">{name}</dt>
      <dd className="min-w-0 whitespace-pre-wrap wrap-anywhere">{text}</dd>
    </div>
  );
}

// 만료 시각과 남은 시간. 30초마다 다시 계산한다. 승인 뒤 실행 유효 시간(5분)도 함께 알린다.
function ExpiryLine({ expiresAt }: { expiresAt: string }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 30_000);
    return () => clearInterval(timer);
  }, []);
  return (
    <div className="flex flex-col gap-0.5 text-xs text-muted-foreground">
      <p>
        {formatExpiryTime(expiresAt)}까지 결정해 주세요 · {formatRemaining(expiresAt, now)}
      </p>
      <p>승인하면 5분 안에 실행합니다. 그 뒤에는 다시 승인받습니다.</p>
    </div>
  );
}

// 어디에 하는 일인지와 어느 계정으로 하는지. 계정이 필요 없는 도구면 계정 줄이 없다.
function TargetLines({ target, account }: { target: string; account: string | null }) {
  return (
    <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-2 gap-y-0.5 text-sm">
      <dt className="text-muted-foreground">대상</dt>
      <dd className="font-medium wrap-anywhere">{target}</dd>
      {account !== null && (
        <>
          <dt className="text-muted-foreground">계정</dt>
          <dd className="font-medium wrap-anywhere">{account}</dd>
        </>
      )}
    </dl>
  );
}

// 승인 뒤 외부 실행 결과(영수증 기준). 보여 줄 것이 없으면 그리지 않는다.
function ExecutionLine({ approval }: { approval: Approval }) {
  const text = executionText(approval);
  if (text === null) return null;
  return <p className="text-sm font-medium">{text}</p>;
}
