import { useId } from "react";
import { safeHttpUrl } from "@/lib/stepOutput";

// 외부 서비스가 준 출처 주소 목록(자동화 탭의 감시 결과, '예약 작업' 대화방의 감시 알림).
// http·https 절대 주소만 새 탭 링크로 연다. javascript:·data:·상대 주소는 글자로만 남긴다.
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
