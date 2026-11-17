"use client";

import { LoaderCircle } from "lucide-react";
import { useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import type { StartScope } from "@/lib/connections";

// 4단계 보강: Google 연결 시작 버튼. 예전에는 링크 이동(GET)이었지만, 시작은 서버에 state를 만드는 일이라 POST로 부르고
// 서버가 준 Google 동의 화면 주소로 이동한다(lib/connections.ts의 startGoogleConnection).
// 누르는 즉시 state와 ref 두 겹으로 잠가 한 번만 보낸다. 이동하면 이 화면을 떠나므로 성공 뒤에는 잠금을 풀지 않는다.
export type StartConnection = (scope: StartScope) => Promise<void>;

type Props = {
  label: string;
  scope: StartScope;
  available: boolean;
  onStart: StartConnection;
  primary?: boolean;
};

const BIG_BUTTON = "h-12 w-full min-w-0 text-base";
const START_FALLBACK = "연결을 시작하지 못했습니다. 잠시 후 다시 시도해 주세요.";

export function StartConnectionButton({
  label,
  scope,
  available,
  onStart,
  primary = false,
}: Props) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const locked = useRef(false);

  async function start() {
    if (locked.current) return;
    locked.current = true;
    setBusy(true);
    setError(null);
    try {
      await onStart(scope);
    } catch (caught) {
      locked.current = false;
      setBusy(false);
      setError(caught instanceof Error ? caught.message : START_FALLBACK);
    }
  }

  return (
    <div className="flex flex-col gap-2">
      <Button
        type="button"
        variant={primary ? "default" : "outline"}
        disabled={!available || busy}
        onClick={start}
        className={BIG_BUTTON}
      >
        {label}
      </Button>
      {busy && (
        <p role="status" className="flex items-center gap-2 text-sm text-muted-foreground">
          <LoaderCircle aria-hidden="true" className="size-4 shrink-0 animate-spin" />
          Google 동의 화면으로 이동하는 중…
        </p>
      )}
      {error && (
        <p role="alert" className="break-words text-sm text-destructive">
          {error}
        </p>
      )}
    </div>
  );
}
