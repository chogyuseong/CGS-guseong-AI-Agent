"use client";

import { PencilLine } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { Suggestion } from "@/lib/suggestions";

type SuggestionChipsProps = {
  // 서버가 저장한 구조화된 제안(messages.suggestions, 6단계 보강).
  suggestions: readonly Suggestion[];
  // 제안 문장을 입력창에 채운다. 보내기는 사용자가 읽고 고친 뒤 직접 한다.
  onPick: (text: string) => void;
  disabled?: boolean;
};

// 예약 작업 답변 아래의 제안 버튼(BUILD_SPEC 8.3).
// - 누르면 입력창에 문장을 채울 뿐 보내지 않는다. 제안은 승인이나 권한을 넘기지 않는다(보내면 평소 채팅처럼 승인을 거친다).
// - 제안은 모델이 썼고 외부 글의 영향을 받을 수 있으므로 이름·이유·문장을 모두 글자로만 보여 준다(HTML로 해석하지 않음).
export function SuggestionChips({ suggestions, onPick, disabled = false }: SuggestionChipsProps) {
  if (suggestions.length === 0) return null;
  return (
    <div className="mt-2 flex min-w-0 flex-col gap-2 border-t pt-2">
      <p className="text-xs text-muted-foreground">
        제안 · 누르면 입력창에 채웁니다. 읽고 고친 뒤 직접 보내 주세요. 일정 등록처럼 바뀌는 일은
        다시 승인을 묻습니다.
      </p>
      <ul className="flex min-w-0 flex-col gap-2">
        {suggestions.map((suggestion) => (
          <li
            key={`${suggestion.title}-${suggestion.proposed_user_message}`}
            className="flex min-w-0 flex-col gap-1 rounded-lg border p-2"
          >
            <Button
              type="button"
              variant="outline"
              aria-label={`${suggestion.title}: 입력창에 채우기`}
              onClick={() => onPick(suggestion.proposed_user_message)}
              disabled={disabled}
              className="h-auto min-h-11 w-full min-w-0 justify-start gap-2 whitespace-normal py-2 text-left text-base"
            >
              <PencilLine aria-hidden="true" className="size-4 shrink-0" />
              <span className="min-w-0 flex-1 wrap-anywhere">{suggestion.title}</span>
            </Button>
            {suggestion.reason && (
              <p className="text-xs text-muted-foreground wrap-anywhere">{suggestion.reason}</p>
            )}
            <p className="whitespace-pre-wrap text-sm wrap-anywhere">
              {suggestion.proposed_user_message}
            </p>
          </li>
        ))}
      </ul>
    </div>
  );
}
