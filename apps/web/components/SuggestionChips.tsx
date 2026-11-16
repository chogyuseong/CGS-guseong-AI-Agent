"use client";

import { Check, Send } from "lucide-react";
import { useRef, useState } from "react";
import { Button } from "@/components/ui/button";

type SuggestionChipsProps = {
  // [제안] 줄에서 꺼낸 문장들(lib/suggestions.ts).
  suggestions: string[];
  // 문장을 현재 대화에 일반 채팅 메시지로 보낸다. 실패하면 예외를 던진다(오류 문구는 채팅 화면이 보여 준다).
  onSend: (text: string) => Promise<unknown>;
  // 입력창 등 다른 곳에서 보내는 중이면 true.
  disabled?: boolean;
};

// 예약 작업 답변 아래의 [제안] 버튼.
// - 자동 전송하지 않는다. 사용자가 눌러야만 보낸다.
// - 제안 문장은 모델이 썼고 외부 글(일정 제목·검색 결과)의 영향을 받을 수 있으므로, 무엇을 보내는지 알 수 있게
//   버튼에 문장 전체를 글자로만 보여 준다(HTML로 해석하지 않음). 길면 줄을 바꾼다.
// - 누르는 즉시 ref로 잠가 연타해도 한 번만 보낸다. 보내는 동안은 다른 제안도 막는다. 보낸 제안은 '보냄'으로 막는다.
export function SuggestionChips({ suggestions, onSend, disabled = false }: SuggestionChipsProps) {
  const { sending, sent, send } = useSuggestionSender(onSend, disabled);
  if (suggestions.length === 0) return null;
  return (
    <div className="mt-2 flex min-w-0 flex-col gap-2 border-t pt-2">
      <p className="text-xs text-muted-foreground">
        제안 · 누르면 이 문장을 채팅으로 보냅니다. 일정 등록처럼 바뀌는 일은 다시 승인을 묻습니다.
      </p>
      <ul className="flex min-w-0 flex-col gap-2">
        {suggestions.map((text) => (
          <li key={text} className="min-w-0">
            <SuggestionChip
              text={text}
              state={sent.includes(text) ? "sent" : sending === text ? "sending" : "idle"}
              locked={disabled || sending !== null}
              onClick={() => void send(text)}
            />
          </li>
        ))}
      </ul>
    </div>
  );
}

// 보내는 중인 문장과 보낸 문장을 기억한다. state와 ref 두 겹으로 잠가 같은 틱의 두 번째 클릭도 막는다.
function useSuggestionSender(onSend: SuggestionChipsProps["onSend"], disabled: boolean) {
  const [sending, setSending] = useState<string | null>(null);
  const [sent, setSent] = useState<readonly string[]>([]);
  const locked = useRef(false);
  async function send(text: string) {
    if (locked.current || disabled) return;
    locked.current = true;
    setSending(text);
    try {
      await onSend(text);
      setSent((previous) => [...previous, text]);
    } catch {
      // 실패 문구는 채팅 화면의 전송 오류 줄에 나온다. 여기서는 다시 누를 수 있게 잠금만 푼다.
    } finally {
      locked.current = false;
      setSending(null);
    }
  }
  return { sending, sent, send };
}

type ChipProps = {
  text: string;
  state: "idle" | "sending" | "sent";
  locked: boolean;
  onClick: () => void;
};

// 버튼 이름은 문장 그대로(aria-label). 상태 글자(보내는 중·보냄)는 눈으로 보는 표시다.
function SuggestionChip({ text, state, locked, onClick }: ChipProps) {
  const Icon = state === "sent" ? Check : Send;
  return (
    <Button
      type="button"
      variant="outline"
      aria-label={text}
      onClick={onClick}
      disabled={locked || state === "sent"}
      className="h-auto min-h-11 w-full min-w-0 justify-start gap-2 whitespace-normal py-2 text-left text-base"
    >
      <Icon aria-hidden="true" className="size-4 shrink-0" />
      <span className="min-w-0 flex-1 whitespace-pre-wrap wrap-anywhere">{text}</span>
      {state !== "idle" && (
        <span aria-hidden="true" className="shrink-0 text-xs text-muted-foreground">
          {state === "sent" ? "보냄" : "보내는 중…"}
        </span>
      )}
    </Button>
  );
}
