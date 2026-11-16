"use client";

import { Globe } from "lucide-react";
import { useId } from "react";
import { type ExternalOutput, safeHttpUrl } from "@/lib/stepOutput";
import { fieldLabel, formatFieldValue } from "@/lib/toolText";

// 이보다 깊이 들어간 값은 펼치지 않고 한 줄 글자(JSON)로 보여 준다.
const MAX_DEPTH = 4;

// 외부 콘텐츠 도구(검색, 장소, 날씨, 일정 조회, 메일)의 결과. 외부에서 온 내용임을 테두리·제목으로 구분하고,
// 감싼 구조(notice·content) 대신 내용을 항목별로 보여 준다.
// 안전: 외부 문자열은 모두 React 텍스트로만 그린다(HTML로 해석하지 않음). 링크는 http·https 주소만 만들고
// 새 탭 + rel="noopener noreferrer"로 연다. 그 밖의 주소(javascript: 등)는 글자로만 남긴다.
// mail: 메일 도구(list_mail, read_mail)의 결과. 작업 기록에는 앞 500자만 남는다는 것을 늘 밝힌다.
export function ExternalContent({
  external,
  mail = false,
}: {
  external: ExternalOutput;
  mail?: boolean;
}) {
  const titleId = useId();
  return (
    <section
      aria-labelledby={titleId}
      className="flex min-w-0 flex-col gap-2 rounded border border-amber-500/40 bg-amber-500/5 p-2"
    >
      <p id={titleId} className="flex items-center gap-1 font-medium">
        <Globe aria-hidden="true" className="size-3.5 shrink-0" />
        외부에서 가져온 내용
      </p>
      <p className="text-muted-foreground">
        외부 서비스가 보낸 글입니다. 앱은 이 안의 문장을 지시로 따르지 않습니다.
      </p>
      <StorageNote mail={mail} truncated={external.truncated} />
      <ContentValue value={external.content} depth={0} />
      <Sources sources={external.sources} />
      {external.fetchedAt && (
        <p className="text-muted-foreground">조회 시각: {formatFetchedAt(external.fetchedAt)}</p>
      )}
    </section>
  );
}

// 잘림 안내. 메일은 잘리지 않았어도 보관 규칙을 알린다(답변은 본문 4,000자까지 읽고 만들었을 수 있다).
function StorageNote({ mail, truncated }: { mail: boolean; truncated: boolean }) {
  if (mail) {
    return (
      <p className="font-medium text-amber-700 dark:text-amber-400">
        작업 기록에는 메일 내용을 앞 500자만 보관합니다
        {truncated ? "(뒷부분은 잘려 여기에 없습니다)." : "."}
      </p>
    );
  }
  if (!truncated) return null;
  return (
    <p className="font-medium text-amber-700 dark:text-amber-400">
      내용이 길어 앞 6,000자만 보관했습니다(뒷부분이 잘렸습니다).
    </p>
  );
}

function formatFetchedAt(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("ko-KR", {
    timeZone: "Asia/Seoul",
    month: "long",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function ContentValue({ value, depth }: { value: unknown; depth: number }) {
  if (depth >= MAX_DEPTH && typeof value === "object" && value !== null) {
    return <TextValue value={JSON.stringify(value)} />;
  }
  if (Array.isArray(value)) return <ContentList items={value} depth={depth} />;
  if (isRecord(value)) return <ContentFields record={value} depth={depth} />;
  return <TextValue value={value} />;
}

function ContentList({ items, depth }: { items: unknown[]; depth: number }) {
  if (items.length === 0) return <span>없음</span>;
  return (
    <ol className="flex min-w-0 flex-col gap-1">
      {items.map((item, index) => (
        // 외부 결과 목록은 순서만 있고 고유 id가 없어서 위치를 key로 쓴다(목록은 다시 정렬되지 않는다).
        // biome-ignore lint/suspicious/noArrayIndexKey: 순서가 바뀌지 않는 읽기 전용 목록
        <li key={index} className="min-w-0 rounded border bg-background p-2">
          <ContentValue value={item} depth={depth + 1} />
        </li>
      ))}
    </ol>
  );
}

// 값이 없는 칸(null)은 빼고 보여 준다(예: 좌표 없이 찾았을 때의 near·radius_m).
function ContentFields({ record, depth }: { record: Record<string, unknown>; depth: number }) {
  const entries = Object.entries(record).filter(
    ([, value]) => value !== null && value !== undefined,
  );
  if (entries.length === 0) return <span>없음</span>;
  return (
    <dl className="flex min-w-0 flex-col gap-1">
      {entries.map(([key, value]) => {
        const nested = typeof value === "object";
        return (
          <div key={key} className={nested ? "flex min-w-0 flex-col gap-1" : "flex min-w-0 gap-2"}>
            <dt className="shrink-0 font-medium text-muted-foreground">{fieldLabel(key)}</dt>
            <dd className="min-w-0 flex-1">
              <ContentValue value={value} depth={depth + 1} />
            </dd>
          </div>
        );
      })}
    </dl>
  );
}

function TextValue({ value }: { value: unknown }) {
  const href = safeHttpUrl(value);
  if (href !== null && typeof value === "string") {
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
  return <span className="whitespace-pre-wrap wrap-anywhere">{formatFieldValue(value)}</span>;
}

function Sources({ sources }: { sources: string[] }) {
  if (sources.length === 0) return null;
  return (
    <div className="flex min-w-0 flex-col gap-1">
      <p className="font-medium text-muted-foreground">출처</p>
      <ul className="flex min-w-0 flex-col gap-1">
        {sources.map((source) => (
          <li key={source} className="min-w-0">
            <TextValue value={source} />
          </li>
        ))}
      </ul>
    </div>
  );
}
