// 승인 카드 한 장(EXTERNAL 도구 호출 한 건의 승인·거절).
// - 쓰는 곳: TaskApprovals(→ 채팅의 답변 자리 PendingTask, 답변 아래 TaskSteps, 작업 기록 탭 TaskList).
//   서버 호출은 이 파일이 하지 않고 부모가 준 onDecide(lib/approvals.ts useDecideApproval)가 한다:
//   POST /api/approvals/{approval_id}/approve(본문 confirm_mismatch) · POST .../reject · POST .../revise(본문 note).
// - 근거: BUILD_SPEC 3.4·3.6·10.1 "승인 카드", 절대 원칙 3(승인 없이 EXTERNAL 실행 금지)·10(영수증 기준 성공),
//   DECISIONS 2026-11-17 화면(apps/web) 절(결정된 카드는 결과만 남김, 대상·계정 줄을 감시 배지보다 먼저).
// 고칠 때 주의:
// - status 값(pending·approved·rejected·expired·cancelled)과 오류 code(MISMATCH_CODE =
//   "mismatch_confirmation_required")는 서버 enum·detail.code와 글자로 맞물린다. 바뀌면 pnpm gen:api 뒤 여기와
//   lib/approvals.ts를 같이 고친다. docs/STATE_MACHINES.md의 approval 표도 본다.
// - e2e(approval.spec.ts, progress.spec.ts, widths.spec.ts, dark-mode.spec.ts)가 article 이름 "승인 요청",
//   버튼 이름 "승인"·"거절"·"그래도 승인"·"취소", status 영역의 "승인 처리 중", 배지 "승인함"을 찾는다.
//   문구를 바꾸면 그 파일과 ApprovalCard.test.tsx를 같이 고친다.
// - 버튼 높이 48px(h-12)는 명세의 승인 버튼 기준이다(BUILD_SPEC 10.1 최소 44px보다 크게).
//   approval.spec.ts의 expectTappable이 화면 안에 다 보이는지와 높이를 잰다.
// - '고쳐서 다시' 버튼·메모 칸 이름("고쳐서 다시", "무엇을 고칠까요?", "고쳐 달라고 보내기")은 ApprovalCard.test.tsx가 찾는다.
// 변경 이력:
// - 2026-11-19 이미 처리된 카드를 다시 누르면(changed=false) "다른 화면에서 이미 처리된 카드입니다" 안내.
// - 2026-11-18 '고쳐서 다시' 추가(요구사항 밖, 사용자 결정 DECISIONS 2026-11-18). 메모를 붙인 거절이라 실행은 없고,
//   모델이 고친 계획으로 새 카드를 만든다. 결정된 카드에는 "고쳐 달라고 함" 배지와 적은 내용을 보여 준다.
// - 2026-11-19 새 카드에 '이전 카드와 달라진 점'(서버 changes)을 보인다. 요청하지 않은 날짜·대상 변경을 승인 전에 알아보게.
"use client";

import { LoaderCircle, ShieldCheck, ShieldQuestionMark, ShieldX } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
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
  REVISION_NOTE_MAX,
} from "@/lib/approvals";
import { fieldLabel, formatFieldValue, toolDisplayName } from "@/lib/toolText";

type ApprovalCardProps = {
  // 서버 GET /api/tasks/{id}의 approvals 한 건(ApprovalOut). 결정된 카드도 같은 모양으로 온다.
  approval: Approval;
  // 서버에 결정을 보내고 결정 뒤 승인 상태를 돌려준다. 실패하면 ApprovalActionError를 던진다.
  // 2026-11-19: {status, changed}도 받는다. changed=false면 이 결정이 반영되지 않았다(다른 화면에서 이미 처리됨).
  onDecide: (decision: Decision) => Promise<ApprovalStatus | DecisionOutcome>;
};

type DecisionOutcome = { status: ApprovalStatus; changed: boolean };

// 결정된 카드의 머리 배지. Record라 서버 상태가 하나 늘면(gen:api 뒤) tsc가 여기 빠진 것을 알려 준다.
// "승인함"은 실행 완료가 아니다: 실행 결과는 ExecutionLine(영수증 기준)이 따로 보여 준다.
const DECIDED_LABELS: Record<Exclude<ApprovalStatus, "pending">, string> = {
  approved: "승인함",
  rejected: "거절함",
  expired: "만료됨",
  cancelled: "작업 취소로 닫힘",
};

// 승인 카드 하나. 외부에 흔적을 남기는 도구 호출 한 건을 실행 전에 보여 주고 사용자가 승인·거절한다.
// 여러 건이면 카드도 여러 장이고 건마다 따로 결정한다("모두 승인"은 두지 않는다: 명세의 절대 원칙).
// 2026-11-19(08번): '고쳐서 다시' 뒤 새 카드가 이전 카드와 다른 항목(서버 revisions.revision_changes).
// 요청한 것만 바뀌었는지(예: 시각만) 승인 전에 확인하게 한다. 값은 서버가 만든 글자를 텍스트로만 그린다.
function ChangeList({ changes }: { changes: NonNullable<Approval["changes"]> }) {
  const titleId = useId();
  if (changes.length === 0) return null;
  return (
    <section aria-labelledby={titleId} className="rounded border bg-muted/40 p-2 text-sm">
      <p id={titleId} className="font-medium">
        이전 카드와 달라진 점
      </p>
      <ul className="grid gap-0.5 wrap-anywhere">
        {changes.map((change) => (
          <li key={change.field}>{`${change.field}: ${change.before} → ${change.after}`}</li>
        ))}
      </ul>
    </section>
  );
}

export function ApprovalCard({ approval, onDecide }: ApprovalCardProps) {
  const flow = useDecisionFlow(onDecide);
  // 서버 데이터가 아직 pending이어도 방금 받은 결정 결과가 있으면 그것을 먼저 보여 준다.
  const status = approval.status === "pending" ? (flow.decided ?? "pending") : approval.status;
  return (
    <article
      aria-label="승인 요청"
      className="flex min-w-0 flex-col gap-3 rounded-xl border bg-card p-3 text-card-foreground shadow-sm"
    >
      <CardHeader status={status} revised={approval.revision_note !== null} />
      {/* 2단계 보강: 날짜·대상·계정·변경 전후를 감시 배지보다 먼저, 더 크게 보여 준다(BUILD_SPEC 3.6). */}
      <TargetLines target={approval.target} account={approval.account} />
      {/* summary에는 「」로 감싼 외부 문자열(일정 제목)이 섞여 올 수 있다. 텍스트로만 그리고 긴 글자는 어디서든 줄을 바꾼다. */}
      <p className="text-base font-medium wrap-anywhere">{approval.summary}</p>
      <ChangeList changes={approval.changes ?? []} />
      <GuardVerdictView guard={approval.guard} />
      <ToolInputDetails toolName={approval.tool_name} input={approval.tool_input} />
      {status === "pending" && <ExpiryLine expiresAt={approval.expires_at} />}
      <ExecutionLine approval={approval} />
      {approval.revision_note !== null && (
        <p className="text-sm wrap-anywhere">고칠 내용: {approval.revision_note}</p>
      )}
      {status === "pending" && <DecisionArea approval={approval} flow={flow} />}
      {flow.error && (
        <p role="alert" className="text-sm text-destructive">
          {flow.error}
        </p>
      )}
      {flow.stale && (
        <p role="status" className="text-sm font-medium text-warning">
          {flow.stale}
        </p>
      )}
    </article>
  );
}

type DecisionFlow = ReturnType<typeof useDecisionFlow>;
type BusyAction = "approve" | "reject" | "revise";

function busyActionOf(decision: Decision): BusyAction {
  if (decision.approve) return "approve";
  return decision.revisionNote === undefined ? "reject" : "revise";
}

// 버튼을 누른 순간부터 응답이 올 때까지 잠근다. 상태(state)는 다음 그리기에서야 바뀌므로,
// 같은 순간에 두 번 눌리는 경우를 ref(즉시 바뀌는 값)로 한 번 더 막는다.
// 막는 사고: 연타로 승인 요청이 두 번 가 재개가 두 번 시작되거나, 승인과 거절이 엇갈려 가는 것.
// 결과: busy(누른 버튼: 승인·거절·고쳐서 다시), confirming(mismatch 확인 영역을 열었는지), error(화면 문장),
// decided(방금 받은 상태).
// 서버가 mismatch 확인을 요구하면(409 MISMATCH_CODE) 오류 문장 대신 확인 영역을 연다. 화면 버그로
// needs_mismatch_confirmation을 놓쳐도 서버가 막고 여기서 다시 묻게 된다(DECISIONS 2026-11-17).
function useDecisionFlow(onDecide: ApprovalCardProps["onDecide"]) {
  const [busy, setBusy] = useState<BusyAction | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [decided, setDecided] = useState<ApprovalStatus | null>(null);
  const [stale, setStale] = useState<string | null>(null);
  const locked = useRef(false);

  async function submit(decision: Decision) {
    if (locked.current) return;
    locked.current = true;
    setBusy(busyActionOf(decision));
    setError(null);
    try {
      const outcome = await onDecide(decision);
      const { status, changed } =
        typeof outcome === "string" ? { status: outcome, changed: true } : outcome;
      setDecided(status);
      if (!changed && status !== "pending") setStale(staleText(decision, status));
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

  return { busy, confirming, setConfirming, error, decided, stale, submit };
}

// 2026-11-19(분석 10·54번): 다른 기기·오래된 탭에서 이미 처리된 카드를 눌렀을 때의 안내.
// 서버는 이미 결정된 승인에 다시 요청하면 changed=false와 현재 상태를 준다(apps/api routers/approvals.py).
// 결과 배지만 바뀌면 왜 내가 누른 것과 다른지 알 수 없어, 지금 상태와 반영되지 않은 행동을 함께 말한다.
function staleText(
  decision: Decision,
  status: Exclude<ApprovalStatus, "pending"> | ApprovalStatus,
): string {
  const action =
    decision.revisionNote !== undefined ? "고쳐서 다시" : decision.approve ? "승인" : "거절";
  const current = status === "pending" ? "대기" : DECIDED_LABELS[status];
  return `다른 화면에서 이미 처리된 카드입니다(현재: ${current}). 방금 누른 '${action}'은 반영되지 않았습니다.`;
}

// 대기 중인 카드의 버튼 영역. 거절은 확인 없이 보낸다(서버도 거절에는 확인을 요구하지 않는다).
// '고쳐서 다시'를 누르면 버튼 대신 메모 칸이 열린다(RevisionForm). mismatch 확인 영역이 열려 있으면 그것이 먼저다.
function DecisionArea({ approval, flow }: { approval: Approval; flow: DecisionFlow }) {
  const [revising, setRevising] = useState(false);
  const disabled = flow.busy !== null;
  const approve = () => {
    // mismatch면 바로 보내지 않고 카드 안에서 한 번 더 묻는다.
    if (approval.needs_mismatch_confirmation) flow.setConfirming(true);
    else flow.submit({ approve: true, confirmMismatch: false });
  };
  const reject = () => flow.submit({ approve: false, confirmMismatch: false });
  return (
    <div className="flex flex-col gap-2">
      {flow.confirming && <MismatchConfirm flow={flow} disabled={disabled} />}
      {!flow.confirming && revising && (
        <RevisionForm flow={flow} disabled={disabled} onCancel={() => setRevising(false)} />
      )}
      {!flow.confirming && !revising && (
        <>
          <div className="grid grid-cols-2 gap-2">
            <ActionButton label="승인" onClick={approve} disabled={disabled} primary />
            <ActionButton label="거절" onClick={reject} disabled={disabled} />
          </div>
          <ActionButton label="고쳐서 다시" onClick={() => setRevising(true)} disabled={disabled} />
        </>
      )}
      {flow.busy && <BusyNotice action={flow.busy} />}
    </div>
  );
}

// 감시 판정이 mismatch일 때 승인 전에 한 번 더 묻는 영역(BUILD_SPEC 3.6). 네이티브 confirm 창 대신 카드 안에 둔다.
// "그래도 승인"만 confirm_mismatch=true로 보낸다. 막는 사고: 요청과 다른 외부 행동을 한 번의 탭으로 승인하는 것.
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

// '고쳐서 다시'의 메모 칸. 보내면 이 카드는 거절되고 모델이 적은 내용대로 고친 새 카드를 만든다.
// 앞뒤 공백만 지워 보낸다(서버도 같은 규칙으로 1~300자를 검사). 빈 메모로는 보낼 수 없다.
// 모델이 다른 칸까지 바꿀 수 있으므로 새 카드를 다시 확인하라고 안내한다(실행은 새 카드 승인 뒤에만).
function RevisionForm(props: { flow: DecisionFlow; disabled: boolean; onCancel: () => void }) {
  const [note, setNote] = useState("");
  const inputId = useId();
  const trimmed = note.trim();
  const send = () =>
    props.flow.submit({ approve: false, confirmMismatch: false, revisionNote: trimmed });
  return (
    <div className="flex flex-col gap-2 rounded-lg border p-2">
      <label htmlFor={inputId} className="text-sm font-medium">
        무엇을 고칠까요?
      </label>
      <textarea
        id={inputId}
        value={note}
        maxLength={REVISION_NOTE_MAX}
        onChange={(event) => setNote(event.target.value)}
        placeholder="예: 3시로 바꿔 줘"
        rows={2}
        className="min-h-11 w-full rounded-md border bg-background px-3 py-2 text-base"
      />
      <p className="text-xs text-muted-foreground">
        이 카드는 거절되고, 고친 내용으로 새 승인 카드가 나옵니다. 새 카드를 확인한 뒤 승인해
        주세요.
      </p>
      <div className="grid grid-cols-2 gap-2">
        <ActionButton
          label="고쳐 달라고 보내기"
          onClick={send}
          disabled={props.disabled || trimmed === ""}
          primary
        />
        <ActionButton label="취소" onClick={props.onCancel} disabled={props.disabled} />
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

// 처리 중 안내(role=status). 승인 문구의 "승인 처리 중"은 approval.spec.ts가 찾는다.
const BUSY_TEXTS: Record<BusyAction, string> = {
  approve: "승인 처리 중… 작업을 이어서 실행하고 있습니다.",
  reject: "거절 처리 중…",
  revise: "고쳐 달라고 보내는 중… 새 승인 카드를 준비합니다.",
};

function BusyNotice({ action }: { action: BusyAction }) {
  return (
    <p role="status" className="flex items-center gap-2 text-sm text-muted-foreground">
      <LoaderCircle aria-hidden="true" className="size-4 shrink-0 animate-spin" />
      {BUSY_TEXTS[action]}
    </p>
  );
}

// 카드 머리: 대기 중이면 안내 한 줄, 결정됐으면 상태 배지(색만이 아니라 글자로도 상태를 알린다, BUILD_SPEC 10.1).
// revised: 메모가 있는 거절('고쳐서 다시')이면 "거절함" 대신 "고쳐 달라고 함"(서버 상태는 rejected 그대로).
function CardHeader({ status, revised }: { status: ApprovalStatus; revised: boolean }) {
  if (status === "pending") {
    return <p className="text-sm font-semibold">승인이 필요합니다</p>;
  }
  const label = status === "rejected" && revised ? "고쳐 달라고 함" : DECIDED_LABELS[status];
  return (
    <Badge variant={status === "approved" ? "default" : "secondary"} className="h-6 text-sm">
      {label}
    </Badge>
  );
}

// 감시 에이전트 판정 배지(BUILD_SPEC 3.6 문구): match=초록 'AI 검토: 일치로 추정', mismatch=빨강 'AI 검토: 요청과 다를 수 있음'과
// 이유, unsure=회색 'AI 검토: 확인 필요'. AI의 추정이므로 날짜·대상 줄보다 작게 둔다.
// guard가 null이면 판정 없이 "감시 결과 없음"만 보인다(판정이 없다는 사실을 숨기지 않는다).
// reason은 감시 모델이 쓴 문장이라 텍스트로만 그린다.
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
      <Badge className="bg-success/10 text-success">
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
// 이 타이머는 글자만 바꾼다. 만료 처리(상태를 expired로)는 서버의 1분 검사가 하고 폴링으로 반영된다.
// "5분"은 서버 apps/api/app/agent/plans.py EXECUTE_WINDOW와 맞물린 고정 문구다. 서버 값이 바뀌면 이 문장도 고친다.
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
// 문장은 lib/approvals.ts executionText가 정한다: operation이 succeeded여도 receipt_confirmed가 없으면 "결과 확인 필요".
// 막는 사고: 실제로 실행됐는지 모르는 외부 작업을 "완료"로 보여 주는 것(절대 원칙 10).
function ExecutionLine({ approval }: { approval: Approval }) {
  const text = executionText(approval);
  if (text === null) return null;
  return <p className="text-sm font-medium">{text}</p>;
}
