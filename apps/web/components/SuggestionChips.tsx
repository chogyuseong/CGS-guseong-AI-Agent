// 예약 작업 답변 아래의 제안 버튼 묶음.
// - 쓰는 곳: MessageList의 MessageBubble('예약 작업' 대화방, origin=schedule 답변만, lib/suggestions.ts
//   suggestionMessageIds). 누르면 ChatScreen이 ChatInput에 문장을 채운다(서버 호출 없음).
// - 근거: BUILD_SPEC 8.3 제안, 절대 원칙 7(자동 작업은 READ만, 바꾸는 일은 제안으로), DECISIONS 2026-11-17
//   "제안 버튼은 입력창에 문장을 채우고 포커스만 준다".
// 고칠 때 주의: e2e automation.spec.ts·widths.spec.ts가 버튼 이름 "{title}: 입력창에 채우기"를 찾는다.
//   SuggestionChips.test.tsx도 같이 본다.
"use client";

import { PencilLine } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { Suggestion } from "@/lib/suggestions";
import { withUniqueKeys } from "@/lib/uniqueKeys";

type SuggestionChipsProps = {
  // 서버가 저장한 구조화된 제안(messages.suggestions, 6단계 보강).
  suggestions: readonly Suggestion[];
  // 제안 문장을 입력창에 채운다. 보내기는 사용자가 읽고 고친 뒤 직접 한다.
  onPick: (text: string) => void;
  // 입력창에서 보내는 중이면 true(채운 문장이 전송 중인 입력과 섞이지 않게).
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
        {withUniqueKeys(suggestions, (item) => `${item.title}-${item.proposed_user_message}`).map(
          ({ item: suggestion, key }) => (
            // 제안에는 id가 없어 제목+문장을 key로 쓰고, 같은 제안이 둘이면 "몇 번째 등장"을 붙인다
            // (2026-11-18 수정, lib/uniqueKeys.ts, SuggestionChips.test.tsx).
            <li key={key} className="flex min-w-0 flex-col gap-1 rounded-lg border p-2">
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
          ),
        )}
      </ul>
    </div>
  );
}
