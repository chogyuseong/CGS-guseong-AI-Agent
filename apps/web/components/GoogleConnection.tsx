"use client";

import { CalendarDays, LoaderCircle } from "lucide-react";
import { type ReactNode, useEffect, useRef, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  type Connection,
  formatConnectedDate,
  type MailConsentChange,
  setMailConsent,
  startGoogleConnection,
  useConnections,
  useDisconnectGoogle,
  useMailConsent,
} from "@/lib/connections";
import { MailPermission } from "./MailPermission";
import { type StartConnection, StartConnectionButton } from "./StartConnectionButton";

// 연결 화면(/connections)의 Google 계정 카드: 상태 배지, 요청 권한 안내, 연결·다시 연결·연결 해제, 메일 권한 영역.
//
// 쓰는 곳: app/(tabs)/connections/page.tsx(GoogleConnectionCard). 설정 탭의 'Google 계정 연결' 카드로 들어온다.
// 서버 호출(lib/connections.ts): useConnections → GET /api/connections, useDisconnectGoogle → DELETE /api/connections/google,
//   useMailConsent → PUT·DELETE /api/connections/google/mail-consent, 연결 시작은 StartConnectionButton(POST start).
//   mutation은 성공·실패 모두 연결 목록을 다시 가져온다(실패해도 서버에서는 바뀌었을 수 있다).
// 근거: BUILD_SPEC 9·10절, DECISIONS 2026-11-17 연결 화면 결정들(요청 권한·7일 재연결 안내를 늘 보임, 해제는 화면 안 확인,
//   연결된 상태에서도 [다시 연결]), 7단계 보강(reconnect_required, mail_consent, mail_enabled).
//
// 고칠 때 주의:
// - 상태 배지 글자는 서버 ConnectionOut의 connected·reconnect_required·available 조합으로 정한다(statusText).
//   서버가 이 칸을 바꾸면 pnpm gen:api 뒤 여기와 MailPermission.tsx를 같이 본다.
// - PERMISSION_LINES의 권한 설명은 서버가 요청하는 OAuth 범위(apps/api/app/google_auth.py)와 맞아야 한다.
//   범위를 바꾸면 이 문장도 고친다. "약 7일"은 Google 테스트 상태 앱의 refresh token 만료 기간이다.
// - 테스트: components/GoogleConnection.test.tsx(GoogleConnectionView에 상태를 직접 넣음), e2e/connections.spec.ts.

const PERMISSION_LINES = [
  "기본 연결이 요청하는 권한: 내 캘린더의 일정 보기·만들기·고치기·지우기. 메일은 요청하지 않습니다(메일 읽기는 아래에서 따로 추가합니다).",
  "일정을 만들기·고치기·지우기 전에는 매번 승인을 묻습니다.",
  "Google의 테스트 상태 앱은 약 7일마다 연결이 풀릴 수 있습니다. 그때는 이 화면에서 다시 연결해 주세요.",
];
const NOT_CONFIGURED_TEXT =
  "서버에 Google 연결 설정(.env의 GOOGLE_CLIENT_ID 등)이 없어 지금은 연결할 수 없습니다. 서버 설정을 마치면 연결할 수 있습니다.";
const NO_CALENDAR_TEXT =
  "캘린더 권한이 없어 일정 도구를 쓸 수 없습니다. 다시 연결하면서 캘린더 일정 항목을 허용해 주세요.";
// 7단계 보강: 토큰 갱신이 invalid_grant(만료·Google 계정에서 권한 철회)로 실패한 상태. 처음부터 연결 안 된 것과 구분한다.
const RECONNECT_TEXT =
  "Google 연결이 만료되었거나 Google 계정에서 권한이 취소되었습니다. 캘린더·메일을 쓰려면 다시 연결해 주세요. 승인을 기다리던 일정 작업은 실행되지 않습니다(다시 연결한 뒤 새로 요청해 주세요).";
const DISCONNECT_FALLBACK = "연결을 해제하지 못했습니다. 잠시 후 다시 시도해 주세요.";
const BIG_BUTTON = "h-12 w-full min-w-0 text-base";

// 서버 상태를 읽어 화면에 넘기는 부분. 화면(GoogleConnectionView)은 서버를 몰라 상태마다 테스트할 수 있다.
export function GoogleConnectionCard() {
  const connections = useConnections();
  const disconnect = useDisconnectGoogle();
  const consent = useMailConsent();
  const google = connections.data?.find((item) => item.provider === "google");
  // 목록은 왔는데 google 항목이 없으면(서버 모양이 바뀜) "확인 중"에 멈추지 않고 오류로 보여 준다.
  const missing = connections.data !== undefined && google === undefined;
  return (
    <GoogleConnectionView
      connection={google}
      loadError={connections.isError || missing}
      onDisconnect={() => disconnect.mutateAsync()}
      onMailConsent={(agree) => consent.mutateAsync(agree)}
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
  // 7단계 보강: 메일 데이터 처리 동의(true)·철회(false). 실패하면 한국어 문장을 담은 Error를 던진다.
  onMailConsent?: MailConsentChange;
};

// 카드 화면. 서버를 모르므로 테스트가 상태(connection)와 콜백을 직접 넣는다. onStart·onMailConsent를 주지 않으면
// 실제 lib 함수를 쓴다(GoogleConnectionCard는 onMailConsent에 캐시 무효화가 붙은 mutation을 넘긴다).
export function GoogleConnectionView({
  connection,
  loadError,
  onDisconnect,
  onStart = startGoogleConnection,
  onMailConsent = setMailConsent,
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
      <ConnectionBody
        connection={connection}
        loadError={loadError}
        flow={flow}
        onStart={onStart}
        onMailConsent={onMailConsent}
      />
      {flow.done && (
        <p role="status" className="text-sm">
          Google 연결을 해제했습니다.
        </p>
      )}
    </section>
  );
}

// 배지 글자. 순서가 중요하다: 재연결 필요 상태는 connected=false로 오므로 "연결 안 됨"보다 먼저 본다.
function statusText(connection: Connection): string {
  if (connection.connected) return "연결됨";
  if (connection.reconnect_required) return "다시 연결 필요";
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
// 결과: confirming(확인 영역 열림), busy(요청 중), error(lib가 만든 한국어 문장), done(성공 안내 표시).
// 막는 사고: 해제 버튼을 실수로 한 번 눌러 바로 연결이 끊기는 일(대기 중 일정 작업까지 무효가 된다).
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
  onMailConsent: MailConsentChange;
};

// 상태별 본문: 불러오는 중·오류 / 다시 연결 필요 / 연결 안 됨 / 연결됨. 메일 권한 영역은 어느 상태에서나 보인다
// (연결 전에 메일 권한 추가를 누르면 캘린더와 함께 연결된다). 연결 해제 버튼은 연결됨·재연결 필요 상태에만 둔다.
function ConnectionBody({ connection, loadError, flow, onStart, onMailConsent }: BodyProps) {
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
  const mail = (
    <MailPermission connection={connection} onStart={onStart} onConsent={onMailConsent} />
  );
  if (connection.reconnect_required) {
    return (
      <ReconnectBody
        notConfigured={notConfigured}
        mail={mail}
        flow={flow}
        onStart={onStart}
        available={connection.available}
      />
    );
  }
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
        {mail}
      </>
    );
  }
  return (
    <>
      <ConnectedDetails connection={connection} />
      {notConfigured}
      {mail}
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

// 7단계 보강: 다시 연결 필요. 이유를 먼저 알리고 [다시 연결]을 크게, 연결 해제(남은 기록 지우기)도 둔다.
// 서버는 이 상태에서 토큰을 이미 비웠고 대기 중 일정 계획을 connection_revoked로 무효화했다(DECISIONS 2026-11-17 재연결).
// 그래서 RECONNECT_TEXT가 "승인을 기다리던 일정 작업은 실행되지 않습니다"라고 알린다.
function ReconnectBody(props: {
  notConfigured: ReactNode;
  mail: ReactNode;
  flow: DisconnectFlow;
  onStart: StartConnection;
  available: boolean;
}) {
  return (
    <>
      <p role="alert" className="break-words text-sm font-medium text-destructive">
        {RECONNECT_TEXT}
      </p>
      {props.notConfigured}
      <StartConnectionButton
        label="다시 연결"
        scope={undefined}
        available={props.available}
        onStart={props.onStart}
        primary
      />
      {props.mail}
      <DisconnectArea flow={props.flow} />
    </>
  );
}

// 연결됨 상태의 세부. 서버가 calendar=false를 주면 일정 도구를 쓸 수 없으므로 다시 연결하라고 알린다
// ([다시 연결]을 이 상태에서도 두는 이유 중 하나, DECISIONS 2026-11-17).
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

// 해제 확인(네이티브 confirm 없음). 문구는 해제가 기록 삭제가 아니라는 것과 대기 중 일정 작업이 무효가 된다는 것을
// 알린다. 서버 DELETE는 저장된 토큰이 있으면 Google 쪽 철회도 요청한다.
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
