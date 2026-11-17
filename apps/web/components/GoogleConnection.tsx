"use client";

import { CalendarDays, LoaderCircle } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  type Connection,
  formatConnectedDate,
  startGoogleConnection,
  useConnections,
  useDisconnectGoogle,
} from "@/lib/connections";
import { MailPermission } from "./MailPermission";
import { type StartConnection, StartConnectionButton } from "./StartConnectionButton";

const PERMISSION_LINES = [
  "기본 연결이 요청하는 권한: 내 캘린더의 일정 보기·만들기·고치기·지우기. 메일은 요청하지 않습니다(메일 읽기는 아래에서 따로 추가합니다).",
  "일정을 만들기·고치기·지우기 전에는 매번 승인을 묻습니다.",
  "Google의 테스트 상태 앱은 약 7일마다 연결이 풀릴 수 있습니다. 그때는 이 화면에서 다시 연결해 주세요.",
];
const NOT_CONFIGURED_TEXT =
  "서버에 Google 연결 설정(.env의 GOOGLE_CLIENT_ID 등)이 없어 지금은 연결할 수 없습니다. 서버 설정을 마치면 연결할 수 있습니다.";
const NO_CALENDAR_TEXT =
  "캘린더 권한이 없어 일정 도구를 쓸 수 없습니다. 다시 연결하면서 캘린더 일정 항목을 허용해 주세요.";
const DISCONNECT_FALLBACK = "연결을 해제하지 못했습니다. 잠시 후 다시 시도해 주세요.";
const BIG_BUTTON = "h-12 w-full min-w-0 text-base";

// 서버 상태를 읽어 화면에 넘기는 부분. 화면(GoogleConnectionView)은 서버를 몰라 상태마다 테스트할 수 있다.
export function GoogleConnectionCard() {
  const connections = useConnections();
  const disconnect = useDisconnectGoogle();
  const google = connections.data?.find((item) => item.provider === "google");
  // 목록은 왔는데 google 항목이 없으면(서버 모양이 바뀜) "확인 중"에 멈추지 않고 오류로 보여 준다.
  const missing = connections.data !== undefined && google === undefined;
  return (
    <GoogleConnectionView
      connection={google}
      loadError={connections.isError || missing}
      onDisconnect={() => disconnect.mutateAsync()}
    />
  );
}

type ViewProps = {
  connection: Connection | undefined;
  loadError: boolean;
  // 연결을 해제한다. 실패하면 한국어 문장을 담은 Error를 던진다.
  onDisconnect: () => Promise<void>;
  // 연결을 시작한다(POST 후 Google 동의 화면으로 이동). 테스트는 가짜 함수를 넘긴다.
  onStart?: StartConnection;
};

export function GoogleConnectionView({
  connection,
  loadError,
  onDisconnect,
  onStart = startGoogleConnection,
}: ViewProps) {
  const flow = useDisconnectFlow(onDisconnect);
  return (
    <section
      aria-labelledby="google-connection-title"
      className="flex min-w-0 flex-col gap-3 rounded-xl border bg-card p-4 text-card-foreground"
    >
      <div className="flex items-center gap-2">
        <CalendarDays aria-hidden="true" className="size-5 shrink-0" />
        <h2 id="google-connection-title" className="text-base font-semibold">
          Google 계정
        </h2>
        {connection && <StatusBadge connection={connection} />}
      </div>
      <ul className="flex list-disc flex-col gap-1 pl-5 text-sm text-muted-foreground">
        {PERMISSION_LINES.map((line) => (
          <li key={line}>{line}</li>
        ))}
      </ul>
      <ConnectionBody connection={connection} loadError={loadError} flow={flow} onStart={onStart} />
      {flow.done && (
        <p role="status" className="text-sm">
          Google 연결을 해제했습니다.
        </p>
      )}
    </section>
  );
}

function statusText(connection: Connection): string {
  if (connection.connected) return "연결됨";
  return connection.available ? "연결 안 됨" : "서버 설정 없음";
}

function StatusBadge({ connection }: { connection: Connection }) {
  return (
    <Badge variant={connection.connected ? "default" : "secondary"} className="ml-auto h-6 text-sm">
      {statusText(connection)}
    </Badge>
  );
}

type DisconnectFlow = ReturnType<typeof useDisconnectFlow>;

// 해제 버튼 → 화면 안 확인 → 해제. 누르는 즉시 state와 ref 두 겹으로 잠가 한 번만 보낸다(승인 카드와 같은 방식).
function useDisconnectFlow(onDisconnect: ViewProps["onDisconnect"]) {
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const locked = useRef(false);

  async function submit() {
    if (locked.current) return;
    locked.current = true;
    setBusy(true);
    setError(null);
    try {
      await onDisconnect();
      setConfirming(false);
      setDone(true);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : DISCONNECT_FALLBACK);
    } finally {
      locked.current = false;
      setBusy(false);
    }
  }

  function ask() {
    setDone(false);
    setError(null);
    setConfirming(true);
  }

  return { confirming, busy, error, done, submit, ask, cancel: () => setConfirming(false) };
}

type BodyProps = {
  connection: Connection | undefined;
  loadError: boolean;
  flow: DisconnectFlow;
  onStart: StartConnection;
};

function ConnectionBody({ connection, loadError, flow, onStart }: BodyProps) {
  if (connection === undefined) {
    return (
      <p className="text-sm text-muted-foreground">
        {loadError
          ? "연결 상태를 확인하지 못했습니다. 잠시 후 이 화면을 다시 열어 주세요."
          : "연결 상태를 확인하는 중…"}
      </p>
    );
  }
  const notConfigured = !connection.available && (
    <p className="break-words text-sm">{NOT_CONFIGURED_TEXT}</p>
  );
  if (!connection.connected) {
    return (
      <>
        {notConfigured}
        <StartConnectionButton
          label="Google 계정 연결"
          scope={undefined}
          available={connection.available}
          onStart={onStart}
          primary
        />
        <MailPermission connection={connection} onStart={onStart} />
      </>
    );
  }
  return (
    <>
      <ConnectedDetails connection={connection} />
      {notConfigured}
      <MailPermission connection={connection} onStart={onStart} />
      <StartConnectionButton
        label="다시 연결"
        scope={undefined}
        available={connection.available}
        onStart={onStart}
      />
      <DisconnectArea flow={flow} />
    </>
  );
}

function ConnectedDetails({ connection }: { connection: Connection }) {
  return (
    <>
      <dl className="flex flex-col gap-1 text-sm">
        <div className="flex gap-2">
          <dt className="shrink-0 font-medium">캘린더 권한</dt>
          <dd>{connection.calendar ? "있음" : "없음"}</dd>
        </div>
        {connection.connected_at && (
          <div className="flex gap-2">
            <dt className="shrink-0 font-medium">연결한 날짜</dt>
            <dd>{formatConnectedDate(connection.connected_at)}</dd>
          </div>
        )}
      </dl>
      {!connection.calendar && <p className="text-sm text-destructive">{NO_CALENDAR_TEXT}</p>}
    </>
  );
}

function DisconnectArea({ flow }: { flow: DisconnectFlow }) {
  return (
    <div className="flex flex-col gap-2">
      {flow.confirming ? (
        <DisconnectConfirm flow={flow} />
      ) : (
        <Button type="button" variant="destructive" onClick={flow.ask} className={BIG_BUTTON}>
          연결 해제
        </Button>
      )}
      {flow.busy && (
        <p role="status" className="flex items-center gap-2 text-sm text-muted-foreground">
          <LoaderCircle aria-hidden="true" className="size-4 shrink-0 animate-spin" />
          해제하는 중…
        </p>
      )}
      {flow.error && (
        <p role="alert" className="break-words text-sm text-destructive">
          {flow.error}
        </p>
      )}
    </div>
  );
}

function DisconnectConfirm({ flow }: { flow: DisconnectFlow }) {
  const boxRef = useRef<HTMLDivElement>(null);
  // 폰에서는 확인 영역이 화면 아래로 가려질 수 있어 열릴 때 보이는 곳까지 스크롤한다(jsdom에는 없어서 ?.()).
  useEffect(() => {
    boxRef.current?.scrollIntoView?.({ block: "nearest" });
  }, []);
  return (
    <div
      ref={boxRef}
      className="flex flex-col gap-2 rounded-lg border border-destructive/40 bg-destructive/5 p-2"
    >
      <p className="text-sm font-medium text-destructive">Google 연결을 해제할까요?</p>
      <p className="text-sm">
        해제하면 채팅에서 캘린더 일정을 보거나 만들 수 없고 메일도 읽을 수 없습니다. Google 쪽 권한
        철회도 함께 요청합니다. 승인을 기다리던 일정 작업은 실행되지 않습니다. 대화와 작업 기록은
        지워지지 않습니다(연결 해제는 기록 삭제와 다릅니다).
      </p>
      <div className="grid grid-cols-2 gap-2">
        <Button type="button" onClick={flow.submit} disabled={flow.busy} className={BIG_BUTTON}>
          해제하기
        </Button>
        <Button
          type="button"
          variant="outline"
          onClick={flow.cancel}
          disabled={flow.busy}
          className={BIG_BUTTON}
        >
          취소
        </Button>
      </div>
    </div>
  );
}
