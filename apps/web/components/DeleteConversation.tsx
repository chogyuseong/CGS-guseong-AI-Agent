"use client";

import { LoaderCircle, Trash2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";

// 5단계 보강: 지금 보고 있는 대화를 지운다(BUILD_SPEC 5.5). 화면 안에서 한 번 더 묻고, 무엇이 지워지고
// 무엇이 남는지 숨기지 않는다. 진행 중인 작업이 있으면 서버가 409로 거부하고 그 문장을 그대로 보여 준다.
const BIG_BUTTON = "h-11 w-full min-w-0 text-base";
const DELETE_FALLBACK = "대화를 지우지 못했습니다. 잠시 후 다시 시도해 주세요.";

type Props = {
  // 지운다. 실패하면 한국어 문장을 담은 Error를 던진다.
  onDelete: () => Promise<void>;
};

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
