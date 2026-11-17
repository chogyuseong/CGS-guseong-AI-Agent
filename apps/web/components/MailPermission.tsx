import { Mail } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import type { Connection } from "@/lib/connections";
import { type StartConnection, StartConnectionButton } from "./StartConnectionButton";

// 7단계: 연결 화면의 '메일 읽기 권한' 영역. 기본 연결(캘린더만)과 따로 둔다.
// gmail.readonly는 사용자가 [메일 읽기 권한 추가]를 누를 때만 요청한다(명세 7단계 2번).
const MAIL_LINES = [
  "메일을 읽기만 합니다. 보내기·답장·삭제·라벨 변경은 하지 않습니다.",
  "본문은 4,000자까지만 읽고 첨부는 이름만 봅니다. 작업 기록에는 앞 500자만 남깁니다.",
  "예약 작업과 조건 감시는 메일을 읽지 않습니다. 채팅에서 직접 물을 때만 읽습니다.",
  "Google 동의 화면에 '확인되지 않은 앱' 경고가 뜰 수 있습니다. 직접 만든 테스트 앱이라 뜨는 경고입니다.",
];
const NOT_CONNECTED_TEXT =
  "아직 Google 계정을 연결하지 않았다면, 누를 때 캘린더 권한과 함께 요청해 연결합니다.";
// Google은 합쳐서 받은 권한 중 일부만 철회하는 방법을 제공하지 않는다(docs/DECISIONS.md 7단계 API 절).
const REMOVE_TEXT =
  "메일 권한만 끄는 방법은 없습니다(Google이 권한 일부만 철회하는 기능을 제공하지 않습니다). 메일 권한을 빼려면 연결 해제 후 캘린더만 다시 연결하세요. 연결된 채로 [다시 연결]을 눌러도 메일 권한은 남습니다.";
export function MailPermission({
  connection,
  onStart,
}: {
  connection: Connection;
  onStart: StartConnection;
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
        available={connection.available}
        onStart={onStart}
      />
    </>
  );
}
