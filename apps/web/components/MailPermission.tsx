import { Mail } from "lucide-react";
import { useRef, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { type Connection, MAIL_DISABLED_TEXT, type MailConsentChange } from "@/lib/connections";
import { type StartConnection, StartConnectionButton } from "./StartConnectionButton";

// 7단계: 연결 화면의 '메일 읽기 권한' 영역. 기본 연결(캘린더만)과 따로 둔다.
// gmail.readonly는 사용자가 [메일 읽기 권한 추가]를 누를 때만 요청한다(명세 7단계 2번).
// 7단계 보강: 메일 데이터 처리 동의(동의 전에는 서버가 메일을 읽지 않는다)와 서버의 메일 기능 플래그를 보여 준다.
const MAIL_LINES = [
  "메일을 읽기만 합니다. 보내기·답장·삭제·라벨 변경은 하지 않습니다.",
  "본문은 4,000자까지만 읽고 첨부는 이름만 봅니다. 작업 기록에는 메일 내용을 남기지 않습니다.",
  "예약 작업과 조건 감시는 메일을 읽지 않습니다. 채팅에서 직접 물을 때만 읽습니다.",
  "Google 동의 화면에 '확인되지 않은 앱' 경고가 뜰 수 있습니다. 직접 만든 테스트 앱이라 뜨는 경고입니다.",
];
const NOT_CONNECTED_TEXT =
  "아직 Google 계정을 연결하지 않았다면, 누를 때 캘린더 권한과 함께 요청해 연결합니다.";
// Google은 합쳐서 받은 권한 중 일부만 철회하는 방법을 제공하지 않는다(docs/DECISIONS.md 7단계 API 절).
const REMOVE_TEXT =
  "메일 권한만 끄는 방법은 없습니다(Google이 권한 일부만 철회하는 기능을 제공하지 않습니다). 메일 권한을 빼려면 연결 해제 후 캘린더만 다시 연결하세요. 연결된 채로 [다시 연결]을 눌러도 메일 권한은 남습니다.";
// 동의 안내: 무엇이 어디로 가고 무엇이 남는지(BUILD_SPEC 5.2 "메일 원문: 사용자 동의 후 LLM 전송").
const CONSENT_LINES = [
  "채팅에서 메일을 찾거나 읽으면 보낸 사람·제목·미리보기·본문(최대 4,000자)이 답변을 만들기 위해 AI 모델 제공자(Anthropic)에게 전송됩니다.",
  "작업 기록에는 메일 내용을 남기지 않습니다. 이어지는 대화를 위해 앞 500자만 암호화해 보관하고 작업이 끝나고 7일 뒤 지웁니다. 화면에 보인 답변은 대화를 지울 때까지 남습니다.",
  "메일을 읽은 대화에서는 웹 검색·장소·날씨를 쓰지 않습니다(메일 내용이 검색어로 나가지 않게).",
  "동의하기 전에는 메일 권한이 있어도 메일을 읽지 않습니다. 동의는 언제든 철회할 수 있습니다(Google 쪽 메일 권한은 그대로이며, 빼려면 연결을 해제합니다).",
];
const CONSENT_FALLBACK = "동의 상태를 저장하지 못했습니다. 잠시 후 다시 시도해 주세요.";

export function MailPermission({
  connection,
  onStart,
  onConsent,
}: {
  connection: Connection;
  onStart: StartConnection;
  onConsent: MailConsentChange;
}) {
  const granted = connection.connected && connection.mail;
  return (
    <section
      aria-labelledby="mail-permission-title"
      className="flex min-w-0 flex-col gap-2 rounded-lg border p-3"
    >
      <div className="flex items-center gap-2">
        <Mail aria-hidden="true" className="size-4 shrink-0" />
        <h3 id="mail-permission-title" className="text-sm font-semibold">
          메일 읽기 권한
        </h3>
        <Badge variant={granted ? "default" : "secondary"} className="ml-auto h-6 text-sm">
          {granted ? "있음" : "없음"}
        </Badge>
      </div>
      <ul className="flex list-disc flex-col gap-1 pl-5 text-sm text-muted-foreground">
        {MAIL_LINES.map((line) => (
          <li key={line}>{line}</li>
        ))}
      </ul>
      {!connection.mail_enabled && (
        <p className="break-words text-sm text-destructive">{MAIL_DISABLED_TEXT}</p>
      )}
      {connection.mail_enabled && (
        <MailConsent consented={connection.mail_consent} onConsent={onConsent} />
      )}
      {granted ? (
        <p className="break-words text-sm">{REMOVE_TEXT}</p>
      ) : (
        <AddMailAction connection={connection} onStart={onStart} />
      )}
    </section>
  );
}

// 4단계 보강: POST로 시작해 서버가 준 Google 동의 화면 주소로 이동한다(StartConnectionButton).
function AddMailAction({
  connection,
  onStart,
}: {
  connection: Connection;
  onStart: StartConnection;
}) {
  return (
    <>
      {!connection.connected && <p className="text-sm">{NOT_CONNECTED_TEXT}</p>}
      <StartConnectionButton
        label="메일 읽기 권한 추가"
        scope="mail"
        available={connection.available && connection.mail_enabled}
        onStart={onStart}
      />
    </>
  );
}

// 동의·철회 버튼. 누르는 즉시 state와 ref 두 겹으로 잠가 한 번만 보낸다(연결 해제 버튼과 같은 방식).
function useConsentFlow(onConsent: MailConsentChange) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const locked = useRef(false);

  async function submit(agree: boolean) {
    if (locked.current) return;
    locked.current = true;
    setBusy(true);
    setError(null);
    try {
      await onConsent(agree);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : CONSENT_FALLBACK);
    } finally {
      locked.current = false;
      setBusy(false);
    }
  }

  return { busy, error, submit };
}

function MailConsent({
  consented,
  onConsent,
}: {
  consented: boolean;
  onConsent: MailConsentChange;
}) {
  const flow = useConsentFlow(onConsent);
  return (
    <section
      aria-labelledby="mail-consent-title"
      className="flex min-w-0 flex-col gap-2 rounded-md bg-muted/40 p-2"
    >
      <div className="flex items-center gap-2">
        <h4 id="mail-consent-title" className="text-sm font-semibold">
          메일 데이터 처리 동의
        </h4>
        <Badge variant={consented ? "default" : "secondary"} className="ml-auto h-6 text-sm">
          {consented ? "동의함" : "동의 안 함"}
        </Badge>
      </div>
      <ul className="flex list-disc flex-col gap-1 pl-5 text-sm break-words">
        {CONSENT_LINES.map((line) => (
          <li key={line}>{line}</li>
        ))}
      </ul>
      <Button
        type="button"
        variant={consented ? "outline" : "default"}
        disabled={flow.busy}
        onClick={() => flow.submit(!consented)}
        className="h-11 w-full min-w-0 text-base"
      >
        {consented ? "동의 철회" : "메일 데이터 처리에 동의"}
      </Button>
      {flow.error && (
        <p role="alert" className="break-words text-sm text-destructive">
          {flow.error}
        </p>
      )}
    </section>
  );
}
