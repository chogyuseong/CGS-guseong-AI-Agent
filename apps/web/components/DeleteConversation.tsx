// 대화 지우기 버튼과 화면 안 확인 영역.
// - 쓰는 곳: ConversationSheet. 실제 삭제는 부모가 준 onDelete(lib/chat.ts deleteConversation →
//   DELETE /api/conversations/{conversation_id})가 한다.
// - 근거: BUILD_SPEC 5.5(대화 삭제와 백업 제거 지연 표시, "즉시 완전 삭제"라고 말하지 않기), DECISIONS 2026-11-17
//   대화 삭제(진행 중 작업이 있으면 409), docs/DATA_POLICY.md(무엇이 지워지고 남는지).
// 고칠 때 주의:
// - 확인 목록 네 줄은 서버의 실제 삭제 범위와 맞물린다(기억·외부 결과는 남고, 백업은 보관 기간 뒤).
//   서버 삭제 범위나 DATA_POLICY가 바뀌면 이 문장도 고친다.
// - e2e conversation-delete.spec.ts가 버튼 "이 대화 지우기", group "대화 지우기 확인", 버튼 "지우기"를 찾는다.
//   DeleteConversation.test.tsx도 같이 본다.
"use client";

import { LoaderCircle, Trash2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";

// 5단계 보강: 지금 보고 있는 대화를 지운다(BUILD_SPEC 5.5). 화면 안에서 한 번 더 묻고, 무엇이 지워지고
// 무엇이 남는지 숨기지 않는다. 진행 중인 작업이 있으면 서버가 409로 거부하고 그 문장을 그대로 보여 준다.
const BIG_BUTTON = "h-11 w-full min-w-0 text-base";
// Error가 아닌 것이 던져졌을 때의 문장. lib/chat.ts DELETE_CONVERSATION_FALLBACK과 같은 글자다.
const DELETE_FALLBACK = "대화를 지우지 못했습니다. 잠시 후 다시 시도해 주세요.";

type Props = {
  // 지운다. 실패하면 한국어 문장을 담은 Error를 던진다.
  onDelete: () => Promise<void>;
};

// 처음엔 버튼 하나, 누르면 확인 영역(네이티브 confirm 없음). 지우기는 state와 ref 두 겹으로 잠가 연타해도 한 번만 보낸다.
// 실패하면(409 진행 중 작업 포함) 서버 문장을 그대로 보이고 확인 영역을 유지해 다시 누를 수 있게 한다.
// 성공 뒤 화면 이동은 부모(onDelete 안)가 한다.
export function DeleteConversation({ onDelete }: Props) {
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const locked = useRef(false);

  async function submit() {
    if (locked.current) return;
    locked.current = true;
    setBusy(true);
    setError(null);
    try {
      await onDelete();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : DELETE_FALLBACK);
    } finally {
      locked.current = false;
      setBusy(false);
    }
  }

  if (!confirming) {
    return (
      <Button
        type="button"
        variant="outline"
        onClick={() => {
          setError(null);
          setConfirming(true);
        }}
        className={BIG_BUTTON}
      >
        <Trash2 aria-hidden="true" />이 대화 지우기
      </Button>
    );
  }
  return (
    <DeleteConfirm
      busy={busy}
      error={error}
      onSubmit={submit}
      onCancel={() => setConfirming(false)}
    />
  );
}

type ConfirmProps = {
  busy: boolean;
  error: string | null;
  onSubmit: () => void;
  onCancel: () => void;
};

// 확인 영역. fieldset + aria-label이라 e2e가 group "대화 지우기 확인"으로 찾는다.
// 막는 사고: 지우면 모든 것이 즉시 사라진다고 오해하는 것(기억·외부 결과·백업은 남는다는 것을 숨기지 않는다).
function DeleteConfirm({ busy, error, onSubmit, onCancel }: ConfirmProps) {
  const boxRef = useRef<HTMLFieldSetElement>(null);
  // 폰에서는 확인 영역이 화면 아래로 가려질 수 있어 열릴 때 보이는 곳까지 스크롤한다(jsdom에는 없어서 ?.()).
  useEffect(() => {
    boxRef.current?.scrollIntoView?.({ block: "nearest" });
  }, []);
  return (
    <fieldset
      ref={boxRef}
      aria-label="대화 지우기 확인"
      className="flex min-w-0 flex-col gap-2 rounded-lg border border-destructive/40 bg-destructive/5 p-3"
    >
      <p className="text-sm font-medium text-destructive">이 대화를 지울까요?</p>
      <ul className="flex list-disc flex-col gap-1 pl-5 text-sm">
        <li>메시지와 작업 기록(단계·승인·실행 기록)을 지웁니다. 되돌릴 수 없습니다.</li>
        <li>이 대화에서 만든 기억은 남습니다. 지우려면 기억 탭에서 따로 지우세요.</li>
        <li>이미 바꾼 캘린더 일정 같은 외부 결과는 그대로 남습니다.</li>
        <li>백업에는 바로 지워지지 않고 보관 기간이 지나야 사라집니다.</li>
      </ul>
      <div className="grid grid-cols-2 gap-2">
        <Button
          type="button"
          variant="destructive"
          onClick={onSubmit}
          disabled={busy}
          className={BIG_BUTTON}
        >
          지우기
        </Button>
        <Button
          type="button"
          variant="outline"
          onClick={onCancel}
          disabled={busy}
          className={BIG_BUTTON}
        >
          취소
        </Button>
      </div>
      {busy && (
        <p role="status" className="flex items-center gap-2 text-sm text-muted-foreground">
          <LoaderCircle aria-hidden="true" className="size-4 shrink-0 animate-spin" />
          지우는 중…
        </p>
      )}
      {error && (
        <p role="alert" className="break-words text-sm text-destructive">
          {error}
        </p>
      )}
    </fieldset>
  );
}
