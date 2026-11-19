// 맡은 일: 채팅 단계의 영화 추천 카드(recommend_movies 결과) 표시. 영화 비서 C 둘째 단계(2026-11-19).
// 서버가 추천 영화가 이번 상영작 조회 목록 안에 있는지 확인하고, 사실 칸(장르·상영 시간·등급·감독·배우·관객 수)은
//   KOBIS 조회 기록에서, 평점만 TMDB에서 채운다(apps/api/app/tools/boxoffice.py recommend_card).
//   화면은 받은 글자를 텍스트로만 그리고 다시 계산하지 않는다.
// 출처 표기: KOBIS는 늘, TMDB는 서버가 "출처 표기"를 줄 때만 로고(public/tmdb-logo.svg, TMDB 공식 로고 페이지의
//   짧은 파란 로고)와 문구를 붙인다(TMDB 이용 조건: 로고는 앱 표시보다 덜 눈에 띄게).
// 쓰는 곳: components/StepTimeline.tsx. 테스트: components/MovieRecommendCard.test.tsx, e2e movie-recommend.spec.ts.
// 고칠 때 주의: 칸 이름(결론·기준일·추천·제목·이유·장르·상영 시간·등급·감독·배우·누적 관객·평점·조건·다음·예매·극장·주소·
//   출처 표기)은 서버와 글자로 맞물린다. "정보 없음"은 서버 boxoffice.UNKNOWN과 같은 글자다.
import { safeHttpUrl } from "@/lib/stepOutput";
import { withUniqueKeys } from "@/lib/uniqueKeys";

const UNKNOWN = "정보 없음";
// TMDB 출처 문구. 서버 boxoffice.TMDB_NOTICE와 같은 글자다(추천 카드는 서버가 보낸 값을, 조회 단계는 이 값을 쓴다).
const TMDB_NOTICE = "이 서비스는 TMDB API를 사용하지만 TMDB의 보증이나 인증을 받지 않았습니다.";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

// 성공한 recommend_movies 출력({status, data: 카드})에서 카드, 아니면 null.
export function readMovieRecommend(output: unknown): Record<string, unknown> | null {
  if (!isRecord(output) || output.status !== "succeeded" || !isRecord(output.data)) return null;
  return Array.isArray(output.data.추천) ? output.data : null;
}

// 값이 있는 칸만 "앞말 값"으로 이어 붙인다. 모두 비면 빈 글자(줄을 그리지 않음).
function line(movie: Record<string, unknown>, keys: string[], prefix = false): string {
  return keys
    .map((key) => [key, movie[key]] as const)
    .filter(([, value]) => typeof value === "string" && value !== UNKNOWN)
    .map(([key, value]) => (prefix ? `${key} ${value}` : String(value)))
    .join(" · ");
}

// '평점 6.8(평가 4건)'. 평가 수를 함께 보여 표본이 작은 평점을 알아볼 수 있게 한다(2026-11-19). 평점이 없으면 빈 글자.
function ratingText(movie: Record<string, unknown>): string {
  const rating = movie.평점;
  if (typeof rating !== "string" || rating === UNKNOWN) return "";
  const votes = movie["평가 수"];
  return typeof votes === "number"
    ? `평점 ${rating}(평가 ${votes.toLocaleString("ko-KR")}건)`
    : `평점 ${rating}`;
}

// '2024년 제작'(KOBIS 제작 연도, 2026-11-19). 모르면 빈 글자.
function yearText(movie: Record<string, unknown>): string {
  const year = movie["제작 연도"];
  return typeof year === "string" && /^\d{4}$/.test(year) ? `${year}년 제작` : "";
}

function MovieItem({ movie }: { movie: Record<string, unknown> }) {
  const title = String(movie.제목 ?? "");
  const lines = [
    [line(movie, ["장르", "상영 시간", "등급"]), yearText(movie)].filter(Boolean).join(" · "),
    [ratingText(movie), line(movie, ["누적 관객"], true)].filter(Boolean).join(" · "),
    line(movie, ["감독", "배우"], true),
  ].filter(Boolean);
  return (
    <article
      aria-label={title}
      className="flex min-w-0 flex-col gap-1 rounded border p-2 wrap-anywhere"
    >
      <p className="font-medium">{title}</p>
      {typeof movie.이유 === "string" && <p>{movie.이유}</p>}
      {lines.map((text) => (
        <p key={text} className="text-muted-foreground">
          {text}
        </p>
      ))}
    </article>
  );
}

function BookingLinks({ value }: { value: unknown }) {
  const links = (Array.isArray(value) ? value.filter(isRecord) : [])
    .map((item) => ({ name: String(item.극장 ?? ""), href: safeHttpUrl(item.주소) }))
    .filter((item): item is { name: string; href: string } => Boolean(item.name && item.href));
  if (!links.length) return null;
  return (
    <ul aria-label="예매" className="flex flex-wrap gap-x-3 gap-y-1">
      {links.map((link) => (
        <li key={link.name}>
          <a
            href={link.href}
            target="_blank"
            rel="noopener noreferrer"
            className="text-primary underline"
          >
            {`${link.name} 예매 페이지`}
          </a>
        </li>
      ))}
    </ul>
  );
}

export function Attribution({ notice }: { notice: unknown }) {
  return (
    <div className="flex flex-col gap-1 text-xs text-muted-foreground">
      <p>영화 정보: KOBIS(영화관입장권통합전산망)</p>
      {typeof notice === "string" && (
        <p className="flex flex-wrap items-center gap-2">
          {/* biome-ignore lint/performance/noImgElement: 작은 정적 SVG 로고(next/image 최적화가 필요 없음) */}
          <img src="/tmdb-logo.svg" alt="TMDB" width={64} height={8} className="h-2 w-auto" />
          <span>{notice}</span>
        </p>
      )}
    </div>
  );
}

// 상영작 조회(list_now_showing) 출력의 출처.TMDB. 조회 결과 모양이 아니면 null(출처 줄을 그리지 않음).
export function readLookupTmdb(output: unknown): boolean | null {
  const data = isRecord(output) ? output.data : null;
  const content = isRecord(data) ? data.content : null;
  const source = isRecord(content) ? content.출처 : null;
  return isRecord(source) && typeof source.TMDB === "boolean" ? source.TMDB : null;
}

// 조회 단계 아래 출처 줄. 모델이 추천 카드 없이 조회 결과만으로 답해도 TMDB 출처가 보이게(2026-11-19 최종 검토).
export function MovieSourceNote({ tmdb }: { tmdb: boolean }) {
  return <Attribution notice={tmdb ? TMDB_NOTICE : null} />;
}

export function MovieRecommendCard({ card }: { card: Record<string, unknown> }) {
  const movies = Array.isArray(card.추천) ? card.추천.filter(isRecord) : [];
  const basis = [
    typeof card.기준일 === "string" ? `${card.기준일} 박스오피스 기준` : null,
    typeof card.조건 === "string" ? card.조건 : null,
  ].filter(Boolean);
  return (
    <div className="flex min-w-0 flex-col gap-2 rounded-lg border p-3 text-sm">
      {typeof card.결론 === "string" && <p className="font-semibold">{card.결론}</p>}
      {basis.length > 0 && <p className="text-xs text-muted-foreground">{basis.join(" · ")}</p>}
      {withUniqueKeys(movies, (m) => String(m.제목)).map(({ key, item }) => (
        <MovieItem key={key} movie={item} />
      ))}
      {typeof card.다음 === "string" && <p>{card.다음}</p>}
      <BookingLinks value={card.예매} />
      <Attribution notice={card["출처 표기"]} />
    </div>
  );
}
