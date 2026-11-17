// 'use client': 이 컴포넌트는 브라우저에서도 실행된다는 표시다(클릭 처리, 상태, 마이크 같은 브라우저 기능을 씀).
// 표시가 없으면 서버에서만 그려지는 컴포넌트가 되어 window·SpeechRecognition에 손댈 수 없다.
// 표시가 있어도 첫 HTML은 서버에서 한 번 그려지므로, 브라우저 전용 값은 lib/speech.ts처럼 조심해서 읽는다.
"use client";

import { Mic, SendHorizontal, Square } from "lucide-react";
import { type FormEvent, useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import {
  joinTranscript,
  readVoiceNoticeAccepted,
  type SpeechInput,
  saveVoiceNoticeAccepted,
  useSpeechInput,
  VOICE_NOTICE_LINES,
} from "@/lib/speech";

type ChatInputProps = {
  // 보낼 내용을 받아 서버로 보내는 함수. Promise는 "나중에 끝나는 작업"이다(Python의 awaitable).
  // 실패하면 예외를 던지고, 그때는 입력한 내용을 지우지 않는다.
  onSend: (text: string) => Promise<unknown> | unknown;
  pending: boolean;
  // 6단계 보강: 예약 작업 제안을 고르면 이 문장을 입력창에 채운다(보내지 않는다). id가 바뀔 때마다 채운다.
  prefill?: Prefill | null;
};

export type Prefill = { id: number; text: string };

const MAX_LENGTH = 4000;

// 채팅 입력창. 글자 16px(text-base): 아이폰 사파리는 16px 미만 입력칸을 누르면 화면을 확대한다.
// 전송 중에는 입력과 버튼을 막아 같은 요청이 두 번 가지 않게 한다.
// 마이크 버튼: 말한 문장을 입력창 끝에 붙이기만 한다. 보내기는 사용자가 읽고 고친 뒤 직접 한다.
export function ChatInput({ onSend, pending, prefill = null }: ChatInputProps) {
  // useState: 컴포넌트가 기억하는 값과 그 값을 바꾸는 함수. (폼 객체의 cleaned_data를 화면이 직접 들고 있는 느낌)
  const [text, setText] = useState("");
  const trimmed = text.trim();
  const speech = useSpeechInput((spoken) =>
    setText((current) => joinTranscript(current, spoken, MAX_LENGTH)),
  );
  const { cancel } = speech;
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const voiceNotice = useVoiceNotice(speech);

  // 제안을 고르면 입력창을 그 문장으로 바꾸고 포커스한다. 사용자가 읽고 고친 뒤 직접 보낸다.
  useEffect(() => {
    if (prefill === null) return;
    setText(prefill.text.slice(0, MAX_LENGTH));
    inputRef.current?.focus();
  }, [prefill]);

  // 전송 중이 되면 듣기를 취소한다. 보낸 뒤 늦게 온 결과가 비워진 입력창에 들어가지 않게.
  useEffect(() => {
    if (pending) cancel();
  }, [pending, cancel]);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!trimmed || pending) return;
    cancel();
    try {
      await onSend(trimmed);
      setText("");
    } catch {
      // 오류 문구는 부모(ChatScreen)가 보여 준다. 여기서는 입력 내용만 남겨 둔다.
    }
  }

  return (
    <form onSubmit={handleSubmit} className="border-t bg-background p-2">
      {pending && <PendingStatus />}
      {voiceNotice.asking && !pending && (
        <VoiceNotice onAccept={voiceNotice.accept} onCancel={voiceNotice.dismiss} />
      )}
      {speech.listening && <ListeningStatus />}
      {speech.error && (
        <p role="alert" className="break-words px-1 pb-1 text-sm text-destructive">
          {speech.error}
        </p>
      )}
      <div className="flex items-end gap-2">
        <label htmlFor="chat-input" className="sr-only">
          메시지 입력
        </label>
        <textarea
          ref={inputRef}
          id="chat-input"
          rows={1}
          value={text}
          maxLength={MAX_LENGTH}
          disabled={pending}
          onChange={(event) => setText(event.target.value)}
          placeholder="무엇을 도와드릴까요?"
          className="block max-h-40 min-h-11 min-w-0 flex-1 resize-none rounded-lg border bg-background px-3 py-2.5 text-base outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-60"
        />
        {speech.supported && (
          <MicButton listening={speech.listening} disabled={pending} onClick={voiceNotice.press} />
        )}
        <Button type="submit" aria-label="전송" disabled={pending || !trimmed} className="size-11">
          <SendHorizontal aria-hidden="true" className="size-5" />
        </Button>
      </div>
    </form>
  );
}

// 마이크 버튼(44px). aria-pressed: 화면 낭독기에 "눌림/안 눌림"을 알려 주는 켜기·끄기 버튼 표시.
// 듣는 중에는 빨간 정지 모양으로 바뀌고, 다시 누르면 멈춘다.
function MicButton(props: { listening: boolean; disabled: boolean; onClick: () => void }) {
  return (
    <Button
      type="button"
      variant={props.listening ? "destructive" : "outline"}
      aria-label="음성 입력"
      aria-pressed={props.listening}
      disabled={props.disabled}
      onClick={props.onClick}
      className={props.listening ? "size-11 motion-safe:animate-pulse" : "size-11"}
    >
      {props.listening ? (
        <Square aria-hidden="true" className="size-5 fill-current" />
      ) : (
        <Mic aria-hidden="true" className="size-5" />
      )}
    </Button>
  );
}

// 7단계 보강: 처음 마이크를 누르면 바로 듣지 않고 외부 처리 안내를 먼저 보여 준다.
// 안내를 확인하면 기억하고 바로 듣기 시작한다(같은 클릭 안이라 브라우저의 마이크 권한 요청이 막히지 않는다).
function useVoiceNotice(speech: SpeechInput) {
  const [asking, setAsking] = useState(false);

  function press() {
    if (!speech.listening && !readVoiceNoticeAccepted()) {
      setAsking(true);
      return;
    }
    speech.toggle();
  }

  function accept() {
    saveVoiceNoticeAccepted();
    setAsking(false);
    speech.toggle();
  }

  return { asking, press, accept, dismiss: () => setAsking(false) };
}

// 음성 최초 사용 안내(BUILD_SPEC 10.2). 화면을 덮는 창 대신 입력창 바로 위에 펼친다(폰에서 입력창과 함께 보이게).
function VoiceNotice(props: { onAccept: () => void; onCancel: () => void }) {
  return (
    <section
      aria-labelledby="voice-notice-title"
      className="mb-2 flex min-w-0 flex-col gap-2 rounded-lg border bg-muted/40 p-3 text-sm"
    >
      <h2 id="voice-notice-title" className="font-semibold">
        음성 입력 안내
      </h2>
      <ul className="flex list-disc flex-col gap-1 pl-5 break-words">
        {VOICE_NOTICE_LINES.map((line) => (
          <li key={line}>{line}</li>
        ))}
      </ul>
      <div className="grid grid-cols-2 gap-2">
        <Button type="button" onClick={props.onAccept} className="h-11 min-w-0">
          확인하고 음성 입력
        </Button>
        <Button type="button" variant="outline" onClick={props.onCancel} className="h-11 min-w-0">
          취소
        </Button>
      </div>
    </section>
  );
}

function ListeningStatus() {
  return (
    <p role="status" className="px-1 pb-1 text-xs text-muted-foreground">
      듣는 중… 말을 마치면 입력창에 글자가 들어갑니다. 멈추려면 마이크 버튼을 다시 누르세요.
    </p>
  );
}

// 진행 중 표시. role="status"는 화면 낭독기에 바뀐 내용을 알려 주는 영역이라는 뜻이다.
function PendingStatus() {
  return (
    <p role="status" className="px-1 pb-1 text-xs text-muted-foreground">
      답변을 기다리는 중…
    </p>
  );
}
