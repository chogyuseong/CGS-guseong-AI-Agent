"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { BellRing, LoaderCircle } from "lucide-react";
import { useRef, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  browserPush,
  disablePush,
  enablePush,
  PUSH_STATE_LABELS,
  type PushBrowser,
  type PushState,
  type PushStatus,
  pushServer,
  readPushStatus,
} from "@/lib/push";

// 설정 화면의 '알림' 카드: 웹 푸시 상태(6가지)와 켜기·끄기.
//
// 쓰는 곳: app/(tabs)/settings/page.tsx(PushSettings). 상태별 화면은 PushSettingsView(테스트용으로 따로 내보냄).
// 브라우저·서버 일은 lib/push.ts가 한다: readPushStatus(GET /api/push/public-key + 이 기기 구독 확인),
//   enablePush(권한 요청 → 서비스 워커 준비 → 구독 → POST /api/push/subscribe),
//   disablePush(POST /api/push/unsubscribe + 브라우저 구독 해지). 서비스 워커는 public/sw.js.
// 근거: BUILD_SPEC 8.4(푸시), DECISIONS 2026-11-17 푸시 구독 결정들(권한 요청은 클릭 처리 안 첫 동작,
//   상태 6가지, "켜짐"은 지금 서버 키로 만든 구독일 때만, 화면을 열 때 구독을 자동으로 다시 보내지 않음,
//   서버가 저장을 거부하면 브라우저 구독도 해지). 폰에서 확인하는 절차는 docs/PUSH-CHECK.md.
//
// 고칠 때 주의:
// - 상태 이름(insecure·unsupported·server_disabled·denied·on·off)은 lib/push.ts PushState와 맞물린다.
//   상태를 더하면 DESCRIPTIONS(Record라 타입 검사가 빠짐을 잡는다)와 PUSH_STATE_LABELS를 같이 고친다.
// - server_disabled는 서버의 VAPID 설정이 모두 있을 때만 enabled=true가 되는 것에 기대고 있다(DECISIONS 2026-11-17).
// - INTRO의 "대화 내용이 들어가지 않습니다"는 서버 푸시 본문이 고정 문구라는 결정(app/push.py)에 기댄다.
// - 테스트: components/PushSettings.test.tsx, e2e/settings.spec.ts(360px 표시). 실제 수신은 자동화하지 않고 폰에서 본다.

// 알림 상태 쿼리 키. 켜기·끄기가 끝나면 이 키를 무효화해 상태를 다시 읽는다.
const PUSH_STATUS_KEY = ["push", "status"] as const;

const INTRO =
  "승인이 필요할 때, 오래 걸린 작업이 끝났을 때, 작업이 실패했을 때 알려 드립니다. 알림에는 대화 내용이 들어가지 않습니다.";
const IPHONE_GUIDE =
  "아이폰(iOS 16.4 이상)은 Safari에서 공유 버튼 → '홈 화면에 추가'를 한 뒤, 홈 화면의 마이에이전트 아이콘으로 열어야 알림을 켤 수 있습니다.";
const DESCRIPTIONS: Record<PushState, string> = {
  insecure: "알림은 https 주소(또는 이 PC의 localhost)로 열었을 때만 켤 수 있습니다.",
  unsupported: "이 브라우저에서는 알림을 받을 수 없습니다.",
  server_disabled:
    "서버에 알림 설정(VAPID 키)이 없어 지금은 켤 수 없습니다. 서버 설정을 마치면 켤 수 있습니다.",
  denied:
    "알림 권한이 거부되어 있습니다. 브라우저(아이폰은 설정 → 알림) 설정에서 이 사이트의 알림을 허용한 뒤 이 화면으로 돌아와 주세요.",
  on: "이 기기에서 알림을 받습니다.",
  off: "알림이 꺼져 있습니다. 켜면 이 기기로 알림이 옵니다.",
};

// 설정 화면의 알림 영역. 상태를 읽고, 버튼을 눌렀을 때만 권한을 묻는다.
// 상태 읽기는 구독을 서버로 보내지 않는다. 같은 endpoint 재구독은 서버에서 user_id를 바꾸므로, 열기만 했는데
// 이 기기 알림이 지금 로그인한 다른 계정으로 옮겨 가는 사고를 막으려는 것이다(보내는 것은 '알림 켜기'를 눌렀을 때뿐).
// 공개 키는 화면을 열 때 미리 읽어 둔다. 클릭 처리 안에서 키를 기다리면 권한 요청이 사용자 동작 밖으로 밀린다.
export function PushSettings() {
  // 다른 앱(브라우저 설정)에서 권한을 바꾸고 돌아오면 창 포커스 때 다시 읽는다(TanStack Query 기본 동작).
  const status = useQuery({
    queryKey: PUSH_STATUS_KEY,
    queryFn: () => readPushStatus(browserPush(), pushServer),
    retry: false,
  });
  const { busy, error, run } = usePushAction();
  // 서버 키가 없으면(null) 켜기 버튼 자체가 막혀 있으므로 onEnable은 아무것도 하지 않는다.
  const publicKey = status.data?.publicKey ?? null;
  return (
    <PushSettingsView
      status={status.data}
      loadError={status.isError}
      busy={busy}
      error={error}
      onEnable={() => {
        if (publicKey !== null) run((browser) => enablePush(browser, pushServer, publicKey));
      }}
      onDisable={() => run((browser) => disablePush(browser, pushServer))}
    />
  );
}

// 켜기·끄기 실행기. 연속으로 눌려도 한 번만 실행하고(ref 잠금), 끝나면 상태를 다시 읽는다.
// 성공·실패 모두 다시 읽는 이유: 실패해도 브라우저 권한·구독이 바뀌었을 수 있고(권한만 허용됨 등),
// 화면 상태는 늘 실제 브라우저·서버에서 다시 읽은 값으로 그린다(action이 돌려주는 PushState는 쓰지 않는다).
function usePushAction() {
  const queryClient = useQueryClient();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const locked = useRef(false);

  // action은 클릭 처리 안에서 바로(기다리지 않고) 부른다. 권한 요청이 사용자 동작 안에서 일어나야 해서다.
  // 그래서 TanStack Query의 useMutation(실행 전에 한 번 기다림)을 쓰지 않고 상태를 직접 관리한다.
  function run(action: (browser: PushBrowser) => Promise<PushState>) {
    if (locked.current) return;
    locked.current = true;
    setBusy(true);
    setError(null);
    action(browserPush())
      .catch((caught: unknown) => {
        setError(caught instanceof Error ? caught.message : "알림 설정을 바꾸지 못했습니다.");
      })
      .finally(async () => {
        await queryClient.invalidateQueries({ queryKey: PUSH_STATUS_KEY });
        locked.current = false;
        setBusy(false);
      });
  }

  return { busy, error, run };
}

type PushSettingsViewProps = {
  status: PushStatus | undefined;
  loadError: boolean;
  busy: boolean;
  error: string | null;
  onEnable: () => void;
  onDisable: () => void;
};

// 상태별 화면. 서버·브라우저를 모르는 순수 컴포넌트라 상태마다 테스트할 수 있다.
export function PushSettingsView(props: PushSettingsViewProps) {
  return (
    <section
      aria-labelledby="push-settings-title"
      className="flex min-w-0 flex-col gap-3 rounded-xl border bg-card p-4 text-card-foreground"
    >
      <div className="flex items-center gap-2">
        <BellRing aria-hidden="true" className="size-5 shrink-0" />
        <h2 id="push-settings-title" className="text-base font-semibold">
          알림
        </h2>
        {props.status && (
          <Badge
            variant={props.status.state === "on" ? "default" : "secondary"}
            className="ml-auto h-6 text-sm"
          >
            {PUSH_STATE_LABELS[props.status.state]}
          </Badge>
        )}
      </div>
      <p className="text-sm text-muted-foreground">{INTRO}</p>
      <StatusBody {...props} />
    </section>
  );
}

// 상태별 본문. 켜기 버튼은 off와 server_disabled(막힌 채로 보임)에서, 끄기 버튼은 on에서만 보인다.
// denied에는 버튼이 없다: 브라우저는 한 번 거부된 권한을 다시 묻지 않으므로 설정에서 허용하라고만 안내한다.
function StatusBody({
  status,
  loadError,
  busy,
  error,
  onEnable,
  onDisable,
}: PushSettingsViewProps) {
  if (status === undefined) {
    return (
      <p className="text-sm text-muted-foreground">
        {loadError
          ? "알림 상태를 확인하지 못했습니다. 잠시 후 이 화면을 다시 열어 주세요."
          : "알림 상태를 확인하는 중…"}
      </p>
    );
  }
  const { state } = status;
  const showGuide = state === "unsupported" || state === "insecure";
  return (
    <>
      <p className="break-words text-sm">{DESCRIPTIONS[state]}</p>
      {showGuide && <p className="break-words text-sm text-muted-foreground">{IPHONE_GUIDE}</p>}
      {(state === "off" || state === "server_disabled") && (
        <ToggleButton
          label="알림 켜기"
          onClick={onEnable}
          disabled={busy || state === "server_disabled"}
        />
      )}
      {state === "on" && <ToggleButton label="알림 끄기" onClick={onDisable} disabled={busy} />}
      {busy && (
        <p role="status" className="flex items-center gap-2 text-sm text-muted-foreground">
          <LoaderCircle aria-hidden="true" className="size-4 shrink-0 animate-spin" />
          처리 중…
        </p>
      )}
      {error && (
        <p role="alert" className="break-words text-sm text-destructive">
          {error}
        </p>
      )}
    </>
  );
}

// 손가락으로 누르기 쉽게 높이 48px, 칸 너비 전체.
function ToggleButton(props: { label: string; onClick: () => void; disabled: boolean }) {
  return (
    <Button
      type="button"
      onClick={props.onClick}
      disabled={props.disabled}
      className="h-12 w-full text-base"
    >
      {props.label}
    </Button>
  );
}
