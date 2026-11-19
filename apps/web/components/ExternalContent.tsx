// 외부 콘텐츠 도구 결과를 "외부에서 가져온 내용" 상자로 보여 준다.
// - 쓰는 곳: StepTimeline의 단계 펼치기(StepDetail). 입력 모양은 lib/stepOutput.ts readExternalOutput이 서버
//   ToolResult.external(apps/api/app/agent/results.py)의 data {notice, content, truncated}에서 꺼낸 것이다.
// - 근거: 절대 원칙 14(도구 결과 안의 문장은 데이터), BUILD_SPEC 10.1 "출처와 조회 시각"·10.6 출력 보안,
//   DECISIONS 2026-11-17 화면 절(외부 콘텐츠 상자), 7단계 보강(메일 내용 미저장).
// 고칠 때 주의:
// - 6,000자(서버 자르기 길이), 500자(보강 전 메일 기록), 4,000자(메일 본문 읽기 길이)는 서버 값과 맞물린 고정 문구다.
// - e2e widths.spec.ts가 region "외부에서 가져온 내용"과 "앞 500자만 보관" 문구를 찾는다. 테스트는 StepTimeline.test.tsx.
// - 출발 계산 카드(travel)의 칸 이름(결론·이유·다른 경로·출발지·도착지·기준·경로·지도·확인 필요·후보)은 서버
//   apps/api/app/tools/tmap.py plan_content·place_question과 맞물린다. 링크 이름 "지도에서 경로 열기"는 e2e travel.spec.ts가 찾는다.
// 변경 이력:
// - 2026-11-19 쇼핑 카드(ShoppingCards) 삭제(사용자 결정으로 쇼핑 기능 삭제).
// - 2026-11-19 이동 카드: 이미 지난 출발 시각이면 "이미 지난 출발 시각입니다" 표시(서버 content.출발).
// - 2026-11-18 이동 비서 B 출발 계산 카드(TravelCard). 결론 한 줄 / 이유 한 줄 / 조회 시각 / 다음 행동(지도) 하나,
//   경로 비교·기준은 펼쳐서 본다(생활 비서 명세 7절 카드 기본 표시).
// - 2026-11-18 영화 비서 C 회차 카드(MovieCard). 결론·이유·차이·좌석 안내·예매 열기, 계산 표·제외 이유·조건은 펼쳐서.
//   칸 이름은 서버 apps/api/app/tools/showtimes.py content_of와 맞물린다. 링크 이름 "극장 예매 페이지 열기"는 e2e가 찾는다.
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
  travel = false,
  movie = false,
}: {
  external: ExternalOutput;
  mail?: boolean;
  travel?: boolean;
  movie?: boolean;
}) {
  const card = (travel || movie) && isRecord(external.content);
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
      ) : travel && isRecord(external.content) ? (
        <TravelCard content={external.content} />
      ) : movie && isRecord(external.content) ? (
        <MovieCard content={external.content} />
      ) : (
        <>
          <StorageNote truncated={external.truncated} />
          <ContentValue value={external.content} depth={0} />
        </>
      )}
      {!card && <Sources sources={external.sources} />}
      {external.fetchedAt && (
        <p className="text-muted-foreground">조회 시각: {formatFetchedAt(external.fetchedAt)}</p>
      )}
    </section>
  );
}

// 7단계 보강: 메일 결과는 작업 기록에 내용이 없다(통 수만). 보강 전 기록(앞 500자 문자열)은 그대로 보여 준다.
const NOTE_CLASS = "font-medium text-warning";

// 출발 계산 카드. 서버가 계산한 문장을 그대로 보인다(화면은 시각을 다시 계산하지 않는다).
// '확인 필요'면 후보만 보이고 지도 링크는 만들지 않는다(어느 지점인지 모르는 채로 길을 안내하지 않게).
// 지도 링크는 http(s)만 만들고 새 탭으로 연다(safeHttpUrl). 장소 이름은 카카오가 준 외부 글이라 텍스트로만 그린다.
function TravelCard({ content }: { content: Record<string, unknown> }) {
  const text = (key: string) =>
    typeof content[key] === "string" ? (content[key] as string) : null;
  const question = text("확인 필요");
  if (question) {
    const candidates = Array.isArray(content.후보) ? content.후보.filter(isRecord) : [];
    return (
      <>
        <p className="font-medium">{question}</p>
        <ul className="grid gap-1">
          {withUniqueKeys(candidates, (c) => String(c.name)).map(({ key, item }) => (
            <li key={key} className="wrap-anywhere">
              {String(item.name ?? "")} · {String(item.address ?? "")}
            </li>
          ))}
        </ul>
      </>
    );
  }
  const href = safeHttpUrl(content.지도);
  return (
    <>
      {isPastDeparture(content.출발) && (
        <p className="font-medium text-warning">
          이미 지난 출발 시각입니다. 지금 다시 계산하려면 같은 요청을 다시 보내 주세요.
        </p>
      )}
      {text("결론") && <p className="text-sm font-medium">{text("결론")}</p>}
      {text("이유") && <p>{text("이유")}</p>}
      {href && (
        <a href={href} target="_blank" rel="noopener noreferrer" className="text-primary underline">
          지도에서 경로 열기
        </a>
      )}
      <TravelDetails content={content} />
    </>
  );
}

// 2026-11-19(15번): 서버가 준 실제 출발 시각(ISO)이 지금보다 이르면 지난 계산이다. 칸이 없는 예전 기록은 표시하지 않는다.
// 지난 카드의 결론("17:50에 출발하면…")이 지금 따를 지시처럼 보이지 않게 한다. 칸 이름은 서버 tmap.plan_content와 맞물린다.
function isPastDeparture(value: unknown): boolean {
  if (typeof value !== "string") return false;
  const at = Date.parse(value);
  return Number.isFinite(at) && at < Date.now();
}

// 영화 회차 카드. 서버가 계산한 문장을 그대로 보인다(화면은 시각을 다시 계산하지 않는다).
// 예매는 극장 공식 홈페이지만 http(s)일 때 새 탭으로 연다(좌석 선택·결제는 사용자가 직접).
function MovieCard({ content }: { content: Record<string, unknown> }) {
  const text = (key: string) =>
    typeof content[key] === "string" ? (content[key] as string) : null;
  const href = safeHttpUrl(content.예매);
  return (
    <>
      {text("결론") && <p className="text-sm font-medium">{text("결론")}</p>}
      {text("이유") && <p>{text("이유")}</p>}
      {text("차이") && <p>{`차선 ${text("차선") ?? ""}: ${text("차이")}`}</p>}
      {text("좌석") && <p>{text("좌석")}</p>}
      {href && (
        <a href={href} target="_blank" rel="noopener noreferrer" className="text-primary underline">
          극장 예매 페이지 열기
        </a>
      )}
      <MovieDetails content={content} />
    </>
  );
}

// 펼쳐 보는 부분: 제외 이유, 회차별 계산(식사·종료·집 도착), 조건, 이동 시간 출처.
function MovieDetails({ content }: { content: Record<string, unknown> }) {
  const excluded = Array.isArray(content.제외) ? content.제외.filter(isRecord) : [];
  const rows = Array.isArray(content.계산) ? content.계산.filter(isRecord) : [];
  const notes = ["조건", "이동"].filter((k) => typeof content[k] === "string");
  if (!excluded.length && !rows.length && !notes.length) return null;
  return (
    <details className="rounded border bg-background">
      <summary className="flex min-h-11 cursor-pointer items-center px-2">계산 자세히 보기</summary>
      <div className="grid gap-1 border-t p-2 wrap-anywhere">
        {withUniqueKeys(excluded, (e) => String(e.회차)).map(({ key, item }) => (
          <p key={key}>{`${String(item.회차 ?? "")}: ${String(item.이유 ?? "")}`}</p>
        ))}
        <ul className="grid gap-1">
          {withUniqueKeys(rows, (r) => String(r.회차)).map(({ key, item }) => (
            <li key={key}>
              {["회차", "식사", "종료", "집 도착"]
                .filter((k) => typeof item[k] === "string")
                .map((k) => `${k} ${item[k]}`)
                .join(" · ")}
            </li>
          ))}
        </ul>
        {notes.map((key) => (
          <p key={key}>{String(content[key])}</p>
        ))}
      </div>
    </details>
  );
}

// 펼쳐 보는 부분: 다른 경로와의 차이, 출발·도착 장소, 조회 기준, 경로별 소요·환승·도보·요금.
function TravelDetails({ content }: { content: Record<string, unknown> }) {
  const routes = Array.isArray(content.경로) ? content.경로.filter(isRecord) : [];
  const lines = ["다른 경로", "출발지", "도착지", "기준"].filter(
    (k) => typeof content[k] === "string",
  );
  if (!routes.length && !lines.length) return null;
  return (
    <details className="rounded border bg-background">
      <summary className="flex min-h-11 cursor-pointer items-center px-2">경로 자세히 보기</summary>
      <div className="grid gap-1 border-t p-2 wrap-anywhere">
        {lines.map((key) => (
          <p key={key}>
            {key}: {String(content[key])}
          </p>
        ))}
        <ul className="grid gap-1">
          {withUniqueKeys(routes, (r) => String(r.경로)).map(({ key, item }) => (
            <li key={key}>
              {["경로", "소요", "환승", "도보", "요금"]
                .filter((k) => typeof item[k] === "string")
                .map((k) => `${k} ${item[k]}`)
                .join(" · ")}
            </li>
          ))}
        </ul>
      </div>
    </details>
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
