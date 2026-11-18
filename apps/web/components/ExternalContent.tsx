// 외부 콘텐츠 도구 결과를 "외부에서 가져온 내용" 상자로 보여 준다.
// - 쓰는 곳: StepTimeline의 단계 펼치기(StepDetail). 입력 모양은 lib/stepOutput.ts readExternalOutput이 서버
//   ToolResult.external(apps/api/app/agent/results.py)의 data {notice, content, truncated}에서 꺼낸 것이다.
// - 근거: 절대 원칙 14(도구 결과 안의 문장은 데이터), BUILD_SPEC 10.1 "출처와 조회 시각"·10.6 출력 보안,
//   DECISIONS 2026-11-17 화면 절(외부 콘텐츠 상자), 7단계 보강(메일 내용 미저장).
// 고칠 때 주의:
// - 6,000자(서버 자르기 길이), 500자(보강 전 메일 기록), 4,000자(메일 본문 읽기 길이)는 서버 값과 맞물린 고정 문구다.
// - e2e widths.spec.ts가 region "외부에서 가져온 내용"과 "앞 500자만 보관" 문구를 찾는다. 테스트는 StepTimeline.test.tsx.
"use client";

import { Globe } from "lucide-react";
import { useId } from "react";
import { type ExternalOutput, safeHttpUrl, storedMailCount } from "@/lib/stepOutput";
import { fieldLabel, formatFieldValue } from "@/lib/toolText";
import { withUniqueKeys } from "@/lib/uniqueKeys";

// 이보다 깊이 들어간 값은 펼치지 않고 한 줄 글자(JSON)로 보여 준다.
// 외부가 깊게 중첩된 값을 보내도 재귀 그리기가 끝없이 깊어지지 않게 하는 상한이다.
const MAX_DEPTH = 4;

// 외부 콘텐츠 도구(검색, 장소, 날씨, 일정 조회, 메일)의 결과. 외부에서 온 내용임을 테두리·제목으로 구분하고,
// 감싼 구조(notice·content) 대신 내용을 항목별로 보여 준다.
// 안전: 외부 문자열은 모두 React 텍스트로만 그린다(HTML로 해석하지 않음). 링크는 http·https 주소만 만들고
// 새 탭 + rel="noopener noreferrer"로 연다. 그 밖의 주소(javascript: 등)는 글자로만 남긴다.
// mail: 메일 도구(list_mail, read_mail)의 결과. 7단계 보강부터 작업 기록에는 메일 내용이 없다(통 수만).
// 모델용 notice는 일부러 보여 주지 않고, 사람에게 맞는 안내 문장("지시로 따르지 않습니다")을 대신 쓴다.
// 막는 사고: 외부 글 속 "이 링크를 눌러라" 같은 문장을 앱의 안내로 착각하는 것(테두리·제목으로 출처를 구분).
export function ExternalContent({
  external,
  mail = false,
  shopping = false,
}: {
  external: ExternalOutput;
  mail?: boolean;
  shopping?: boolean;
}) {
  const titleId = useId();
  return (
    <section
      aria-labelledby={titleId}
      className="flex min-w-0 flex-col gap-2 rounded border border-warning/40 bg-warning/5 p-2"
    >
      <p id={titleId} className="flex items-center gap-1 font-medium">
        <Globe aria-hidden="true" className="size-3.5 shrink-0" />
        외부에서 가져온 내용
      </p>
      <p className="text-muted-foreground">
        외부 서비스가 보낸 글입니다. 앱은 이 안의 문장을 지시로 따르지 않습니다.
      </p>
      {mail ? (
        <MailContent content={external.content} />
      ) : shopping && isRecord(external.content) ? (
        <ShoppingCards content={external.content} />
      ) : (
        <>
          <StorageNote truncated={external.truncated} />
          <ContentValue value={external.content} depth={0} />
        </>
      )}
      {(!shopping || !isRecord(external.content)) && <Sources sources={external.sources} />}
      {external.fetchedAt && (
        <p className="text-muted-foreground">조회 시각: {formatFetchedAt(external.fetchedAt)}</p>
      )}
    </section>
  );
}

// 7단계 보강: 메일 결과는 작업 기록에 내용이 없다(통 수만). 보강 전 기록(앞 500자 문자열)은 그대로 보여 준다.
const NOTE_CLASS = "font-medium text-warning";

// 2026-11-18 생활 비서 A단계. 정렬·판정은 서버 결과 그대로 두고 긴 추적 URL은 상품명으로 표시한다.
// 이전 기록이나 일부 칸 누락도 나머지 카드를 가리지 않게 한다. 필드 변경은 test_shopping과 함께 확인한다.
function ShoppingCards({ content }: { content: Record<string, unknown> }) {
  return (
    <>
      <p>
        {[content.안내, content["요청 조건"], content["가격 기준"]]
          .filter((v) => typeof v === "string")
          .join(" · ")}
      </p>
      {typeof content["출처 안내"] === "string" && <p>{content["출처 안내"]}</p>}
      {["조건 일치", "확인 필요", "제외 이유"].map((group) => {
        const values = content[group];
        if (!Array.isArray(values) || !values.length) return null;
        return (
          <section key={group} aria-label={group}>
            <p className="font-medium">{group}</p>
            <ul className="grid gap-2">
              {withUniqueKeys(values.filter(isRecord), (row) => String(row.링크)).map(
                ({ key, item }) => (
                  <ShoppingOffer key={key} row={item} />
                ),
              )}
            </ul>
          </section>
        );
      })}
    </>
  );
}

// 사용자가 링크를 열 때만 판매처에 접속한다. 외부 제목은 React 텍스트로 표시한다.
function ShoppingOffer({ row }: { row: Record<string, unknown> }) {
  const href = safeHttpUrl(row.링크);
  const title = typeof row.상품 === "string" ? row.상품 : "상품 보기";
  return (
    <li className="grid gap-1 rounded border bg-background p-2 wrap-anywhere">
      {href ? (
        <a href={href} target="_blank" rel="noopener noreferrer" className="text-primary underline">
          {title}
        </a>
      ) : (
        title
      )}
      {["판매처", "비교 가격", "표시가", "배송비", "단가"].map((key) =>
        typeof row[key] === "string" ? (
          <p key={key}>
            {key} <span>{row[key]}</span>
          </p>
        ) : null,
      )}
      {typeof row.이유 === "string" && <p>{row.이유}</p>}
    </li>
  );
}

// 새 기록({stored: false, mail_count})이면 통 수와 "내용을 저장하지 않음"만, 아니면 보강 전 기록을 그대로 보인다.
function MailContent({ content }: { content: unknown }) {
  const count = storedMailCount(content);
  if (count !== null) {
    return (
      <>
        <p>메일 {count}통을 확인했습니다.</p>
        <p className={NOTE_CLASS}>
          작업 기록에는 메일 내용(보낸 사람·제목·본문)을 저장하지 않습니다. 답변은 본문을
          4,000자까지 읽고 만들었습니다.
        </p>
      </>
    );
  }
  return (
    <>
      <p className={NOTE_CLASS}>
        예전 방식으로 메일 내용을 앞 500자만 보관한 기록입니다(보관 기간이 지나면 지워집니다).
      </p>
      <ContentValue value={content} depth={0} />
    </>
  );
}

// 잘림 안내(6,000자).
function StorageNote({ truncated }: { truncated: boolean }) {
  if (!truncated) return null;
  return <p className={NOTE_CLASS}>내용이 길어 앞 6,000자만 보관했습니다(뒷부분이 잘렸습니다).</p>;
}

// 서버 fetched_at(ISO)을 서울 시각으로. 읽을 수 없는 값이면 받은 글자를 그대로 보인다(화면이 깨지지 않게).
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

// 값 하나를 모양에 따라 목록·칸·글자로 나눠 그린다. depth는 MAX_DEPTH 검사용으로 한 단계마다 1씩 깊어진다.
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

// 글자 하나. http·https 절대 주소면 링크, 아니면 글자. safeHttpUrl이 허용 목록 검사(BUILD_SPEC 10.6)를 한다.
// 막는 사고: 외부 결과 속 javascript: 주소가 눌리는 것(XSS), 새 탭이 window.opener로 이 앱을 바꾸는 것(rel noopener),
// 외부 사이트에 이 앱 주소가 Referer로 넘어가는 것(rel noreferrer).
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

// 서버 ToolResult.sources(빈 문자열은 readExternalOutput이 이미 뺐다). 링크 규칙은 TextValue와 같다.
// 중복을 빼는 SourceLinks와 달리 여기는 서버가 준 그대로 보여 준다. 같은 주소가 두 번 올 수 있어
// key에 "몇 번째 등장"을 붙인다(2026-11-18 수정, lib/uniqueKeys.ts, ExternalContent.test.tsx).
function Sources({ sources }: { sources: string[] }) {
  if (sources.length === 0) return null;
  return (
    <div className="flex min-w-0 flex-col gap-1">
      <p className="font-medium text-muted-foreground">출처</p>
      <ul className="flex min-w-0 flex-col gap-1">
        {withUniqueKeys(sources, (source) => source).map(({ item: source, key }) => (
          <li key={key} className="min-w-0">
            <TextValue value={source} />
          </li>
        ))}
      </ul>
    </div>
  );
}
