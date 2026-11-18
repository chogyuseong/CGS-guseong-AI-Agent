// 출처 주소 목록. 'use client'가 없지만 부모(AutomationItems, MessageList)가 클라이언트 컴포넌트라 거기서 함께 돈다.
// - 쓰는 곳: components/AutomationItems.tsx(감시 last_result.sources), MessageList의 WatchSources(서버 messages.sources).
// - 근거: DECISIONS 2026-11-17 감시 알림 sources(본문 "출처:" 줄 대신 서버가 따로 준 출처), BUILD_SPEC 10.6
//   링크 프로토콜 허용 목록, 절대 원칙 14.
// 고칠 때 주의: e2e automation.spec.ts·widths.spec.ts가 list "출처"(aria-labelledby)와 그 안의 링크 수를 센다.
import { useId } from "react";
import { safeHttpUrl } from "@/lib/stepOutput";

// 외부 서비스가 준 출처 주소 목록(자동화 탭의 감시 결과, '예약 작업' 대화방의 감시 알림).
// http·https 절대 주소만 새 탭 링크로 연다. javascript:·data:·상대 주소는 글자로만 남긴다.
// 막는 사고: 외부 글이 javascript: 주소를 출처로 넣어 누르면 스크립트가 도는 것(XSS).
export function SourceLinks({ sources }: { sources: readonly string[] }) {
  const titleId = useId();
  // 같은 주소는 한 번만 보여 준다(React key가 겹치지 않게).
  const unique = [...new Set(sources)];
  if (unique.length === 0) return null;
  return (
    <div className="flex min-w-0 flex-col gap-1">
      <p id={titleId} className="font-medium text-muted-foreground">
        출처
      </p>
      <ul aria-labelledby={titleId} className="flex min-w-0 flex-col gap-1">
        {unique.map((source) => (
          <li key={source} className="min-w-0">
            <SourceLink value={source} />
          </li>
        ))}
      </ul>
    </div>
  );
}

// href는 브라우저가 해석한 주소(safeHttpUrl 결과), 보이는 글자는 받은 그대로다.
// rel="noopener noreferrer": 새 탭이 window.opener로 이 앱을 바꾸지 못하게, 이 앱 주소가 Referer로 넘어가지 않게.
function SourceLink({ value }: { value: string }) {
  const href = safeHttpUrl(value);
  if (href === null) return <span className="wrap-anywhere">{value}</span>;
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className="text-primary underline wrap-anywhere"
    >
      {value}
    </a>
  );
}
