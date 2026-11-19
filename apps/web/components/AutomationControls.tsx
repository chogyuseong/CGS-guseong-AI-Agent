"use client";

import { LoaderCircle } from "lucide-react";
import { type ReactNode, useEffect, useId, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { type AutomationKind, useDeleteAutomation, useSetActive } from "@/lib/automations";
import { cn } from "@/lib/utils";

// 자동화 화면의 공통 조각: 항목 틀(제목, 켜기·끄기 스위치, 삭제 확인), 안내 줄, 불러오기 오류.
//
// 쓰는 곳: AutomationItems.tsx(ScheduleItem·WatchItem이 AutomationItem으로 감싼다),
//   AutomationList.tsx(FeedbackLine·LoadError). 화면은 app/(tabs)/automation/page.tsx(자동화 탭, 6단계).
// 서버 호출: lib/automations.ts의 useSetActive(PATCH /api/schedules|watches/{id}, 본문 {is_active}),
//   useDeleteAutomation(DELETE 같은 경로). 두 mutation 모두 성공·실패와 관계없이 목록 쿼리를 무효화한다.
// 근거: BUILD_SPEC 8절(예약·감시), DECISIONS 2026-11-17 "자동화 탭"·"삭제는 항목 안에서 한 번 묻는다"
//   ·"화면에서 다시 켤 때도 같은 제한".
//
// 고칠 때 주의:
// - 실패 문구는 서버 문장이 아니라 lib/automations.ts의 automationErrorText가 오류 code
//   (409 limit_reached·duplicate·expired, 404)로 고른 고정 문구다. 여기서는 Error.message를 그대로 보인다.
// - 확인 문구("이 예약 작업을 지울까요?" 등)·스위치 이름("켜기·끄기")은 AutomationList.test.tsx와
//   e2e/automation.spec.ts가 글자로 찾는다. 바꾸면 테스트도 고친다.

// 결과 안내 한 줄. tone에 따라 role(status/alert)과 색이 정해진다.
export type Feedback = { tone: "success" | "error"; text: string };
// 항목이 결과를 목록 위 안내 줄로 올려 보내는 함수(null은 지난 안내 지우기). 안내 줄은 탭에 하나만 둔다.
export type Report = (feedback: Feedback | null) => void;

// Error가 아닌 값이 던져졌을 때만 쓰는 문구(lib 함수는 늘 한국어 Error를 던진다).
const FALLBACK_ERROR = "요청을 처리하지 못했습니다. 잠시 후 다시 시도해 주세요.";
// 모바일에서 누르기 쉬운 높이 44px(h-11). e2e/width-audit.ts가 768px 미만에서 44px 미만 버튼을 잡는다.
const BIG_BUTTON = "h-11 w-full min-w-0 text-base";
// 조사까지 붙인 이름(예약 작업을 / 조건 감시를).
const OBJECT: Record<AutomationKind, string> = { schedule: "예약 작업을", watch: "조건 감시를" };

function errorText(caught: unknown): string {
  return caught instanceof Error ? caught.message : FALLBACK_ERROR;
}

// 누르는 즉시 ref로 잠가 연타해도 한 번만 실행한다(state만으로는 같은 틱의 두 번째 클릭을 못 막는다).
// busy는 어느 동작이 진행 중인지 화면에 알리는 값이고, 실제 중복 차단은 ref가 한다.
// 막는 사고: 삭제를 두 번 보내 두 번째가 404 "이미 지워졌습니다" 오류로 보이는 일, 켜기를 두 번 보내 제자리로 돌아가는 일.
function useLocked() {
  const [busy, setBusy] = useState<"toggle" | "delete" | null>(null);
  const locked = useRef(false);
  async function run(kind: "toggle" | "delete", action: () => Promise<void>) {
    if (locked.current) return;
    locked.current = true;
    setBusy(kind);
    try {
      await action();
    } finally {
      locked.current = false;
      setBusy(null);
    }
  }
  return { busy, run };
}

type ItemProps = {
  kind: AutomationKind;
  id: number;
  title: string;
  active: boolean;
  // 서버 목록 응답의 max_active(rules.py MAX_ACTIVE_SCHEDULES·MAX_ACTIVE_WATCHES). 409 limit_reached 문구에 넣는다.
  maxActive: number;
  // 켤 수 없는 이유(만료 등). 있으면 꺼진 스위치를 막는다.
  cannotEnable?: string;
  badges?: ReactNode;
  report: Report;
  children: ReactNode;
};

// 예약·감시 한 건의 틀. 켜기·끄기와 삭제는 같은 잠금을 써서 한쪽이 진행 중이면 다른 쪽도 막는다.
// 꺼진 항목에 cannotEnable이 있으면 스위치를 막는다(만료된 감시는 서버도 409 expired로 거부한다. 화면은 미리 막을 뿐).
// 켜진 항목은 cannotEnable이 있어도 끌 수 있다.
export function AutomationItem(props: ItemProps) {
  const titleId = useId();
  const actions = useItemActions(props);
  return (
    <li className="flex min-w-0 flex-col gap-2 rounded-xl border bg-card p-3 text-card-foreground">
      <div className="flex min-w-0 items-start gap-2">
        <div className="flex min-w-0 flex-1 flex-col gap-1">
          <h3 id={titleId} className="text-base font-semibold wrap-anywhere">
            {props.title}
          </h3>
          {props.badges}
        </div>
        <ActiveSwitch
          checked={props.active}
          busy={actions.busy === "toggle"}
          disabled={actions.busy !== null || (!props.active && props.cannotEnable !== undefined)}
          describedBy={titleId}
          onToggle={actions.toggle}
        />
      </div>
      {props.children}
      <DeleteArea kind={props.kind} busy={actions.busy} onDelete={actions.remove} />
    </li>
  );
}

// 켜기·끄기·삭제 실행기. 시작할 때 지난 안내를 지우고, 끝나면 성공 문구나 lib가 만든 오류 문구를 report로 올린다.
// 목록 다시 가져오기는 lib의 mutation(onSettled 무효화)이 한다. 삭제에 성공하면 이 항목은 목록에서 사라진다.
function useItemActions({ kind, id, active, maxActive, report }: ItemProps) {
  const setActive = useSetActive(kind);
  const remove = useDeleteAutomation(kind);
  const { busy, run } = useLocked();
  const attempt = (which: "toggle" | "delete", action: () => Promise<unknown>, done: string) =>
    run(which, async () => {
      report(null);
      try {
        await action();
        report({ tone: "success", text: done });
      } catch (caught) {
        report({ tone: "error", text: errorText(caught) });
      }
    });
  return {
    busy,
    toggle: () =>
      attempt(
        "toggle",
        () => setActive.mutateAsync({ id, active: !active, maxActive }),
        `${OBJECT[kind]} ${active ? "껐습니다" : "켰습니다"}.`,
      ),
    remove: () =>
      attempt("delete", () => remove.mutateAsync({ id, maxActive }), `${OBJECT[kind]} 지웠습니다.`),
  };
}

type SwitchProps = {
  checked: boolean;
  busy: boolean;
  disabled: boolean;
  describedBy: string;
  onToggle: () => void;
};

// 켜기·끄기 스위치. 이름은 고정("켜기·끄기")이고 상태는 aria-checked로 알린다. 누를 곳은 44px 이상.
// 어느 항목의 스위치인지는 aria-describedby(항목 제목)로 알린다. 보이는 글자("켜짐" 등)는 aria-hidden이라
// 화면 읽기 프로그램이 상태를 두 번 읽지 않는다.
function ActiveSwitch({ checked, busy, disabled, describedBy, onToggle }: SwitchProps) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label="켜기·끄기"
      aria-describedby={describedBy}
      disabled={disabled}
      onClick={onToggle}
      className="inline-flex min-h-11 shrink-0 items-center gap-2 rounded-md px-1 text-sm outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:opacity-60"
    >
      <span aria-hidden="true" className="w-16 text-right text-muted-foreground">
        {busy ? "바꾸는 중…" : checked ? "켜짐" : "꺼짐"}
      </span>
      <span
        aria-hidden="true"
        className={cn(
          "flex h-6 w-11 items-center rounded-full p-0.5 transition-colors",
          checked ? "bg-primary" : "bg-input",
        )}
      >
        <span
          className={cn(
            "size-5 rounded-full bg-background shadow transition-transform",
            checked && "translate-x-5",
          )}
        />
      </span>
    </button>
  );
}

type DeleteProps = {
  kind: AutomationKind;
  busy: "toggle" | "delete" | null;
  onDelete: () => Promise<void>;
};

// 삭제는 화면 안에서 한 번 묻는다(네이티브 confirm 없음). 서버는 확인 인자를 요구하지 않으므로
// 실수로 지우는 일을 막는 곳은 이 확인뿐이다. 되돌리는 API가 없다(DECISIONS 2026-11-17).
function DeleteArea({ kind, busy, onDelete }: DeleteProps) {
  const [confirming, setConfirming] = useState(false);
  if (!confirming) {
    return (
      <Button
        type="button"
        variant="outline"
        disabled={busy !== null}
        onClick={() => setConfirming(true)}
        className={cn(BIG_BUTTON, "text-destructive")}
      >
        삭제
      </Button>
    );
  }
  return (
    <DeleteConfirm
      kind={kind}
      busy={busy}
      onDelete={onDelete}
      onCancel={() => setConfirming(false)}
    />
  );
}

// 확인 영역. 지우는 동안에는 [지우기]·[취소]를 모두 막는다(지우는 중 취소해도 요청은 이미 갔다).
function DeleteConfirm({ kind, busy, onDelete, onCancel }: DeleteProps & { onCancel: () => void }) {
  const boxRef = useRef<HTMLDivElement>(null);
  // 폰에서는 확인 영역이 가려질 수 있어 열릴 때 보이는 곳까지 스크롤한다(jsdom에는 없어서 ?.()).
  useEffect(() => {
    boxRef.current?.scrollIntoView?.({ block: "nearest" });
  }, []);
  return (
    <div
      ref={boxRef}
      className="flex flex-col gap-2 rounded-lg border border-destructive/40 bg-destructive/5 p-3"
    >
      <p className="text-sm font-medium text-destructive">이 {OBJECT[kind]} 지울까요?</p>
      <p className="text-sm">지우면 되돌릴 수 없습니다. 잠시 멈추려면 지우지 말고 끄세요.</p>
      <div className="grid grid-cols-2 gap-2">
        <Button
          type="button"
          variant="destructive"
          onClick={() => void onDelete()}
          disabled={busy !== null}
          className={BIG_BUTTON}
        >
          지우기
        </Button>
        <Button
          type="button"
          variant="outline"
          onClick={onCancel}
          disabled={busy !== null}
          className={BIG_BUTTON}
        >
          취소
        </Button>
      </div>
      {busy === "delete" && <Deleting />}
    </div>
  );
}

function Deleting() {
  return (
    <p className="flex items-center gap-2 text-sm text-muted-foreground">
      <LoaderCircle aria-hidden="true" className="size-4 shrink-0 animate-spin" />
      지우는 중…
    </p>
  );
}

// 결과 안내 줄. 오류는 긴 목록 아래쪽에서 눌렀어도 보이도록 그 자리까지 스크롤한다.
export function FeedbackLine({ feedback }: { feedback: Feedback | null }) {
  const ref = useRef<HTMLParagraphElement>(null);
  useEffect(() => {
    if (feedback?.tone === "error") ref.current?.scrollIntoView?.({ block: "nearest" });
  }, [feedback]);
  if (feedback === null) return null;
  const error = feedback.tone === "error";
  return (
    <p
      ref={ref}
      role={error ? "alert" : "status"}
      className={cn(
        "rounded-lg border p-3 text-sm wrap-anywhere",
        error ? "border-destructive/40 bg-destructive/5 text-destructive" : "bg-muted",
      )}
    >
      {feedback.text}
    </p>
  );
}

type LoadErrorProps = { message: string; fetching: boolean; onReload: () => void };

// 목록을 불러오지 못했을 때의 오류와 [다시 불러오기]. message는 lib의 fetch 함수가 만든 한국어 문장이다
// (네트워크 오류·"오류 상태코드"). 다시 불러오는 동안에는 버튼을 막아 요청이 겹치지 않게 한다.
export function LoadError({ message, fetching, onReload }: LoadErrorProps) {
  return (
    <div className="flex flex-col gap-2 rounded-lg border border-destructive/40 bg-destructive/5 p-3">
      <p role="alert" className="text-sm text-destructive wrap-anywhere">
        {message}
      </p>
      <Button
        type="button"
        variant="outline"
        onClick={onReload}
        disabled={fetching}
        className={BIG_BUTTON}
      >
        {fetching ? "다시 불러오는 중…" : "다시 불러오기"}
      </Button>
    </div>
  );
}
