// 작업 단계(step)의 출력 읽기. 출력은 서버의 ToolResult({ok, data, error, sources, fetched_at})다.
// 외부 콘텐츠 도구(검색, 장소, 날씨, 일정 조회)의 data는 {notice, content, truncated}로 감싸여 온다
// (apps/api/app/agent/results.py의 ToolResult.external). notice는 모델에게 주는 안내라 화면에는 쓰지 않는다.

export type ExternalOutput = {
  // 외부에서 가져온 내용. 6,000자를 넘어 잘렸으면 JSON 앞부분 문자열이다.
  content: unknown;
  truncated: boolean;
  sources: string[];
  fetchedAt: string | null;
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

// 외부 콘텐츠 모양이 아니면 null. 서버 모양이 바뀌어도 화면이 깨지지 않게 실행 중에 하나씩 확인한다.
export function readExternalOutput(output: unknown): ExternalOutput | null {
  if (!isRecord(output) || !isRecord(output.data)) return null;
  const { notice, content, truncated } = output.data;
  if (typeof notice !== "string" || !("content" in output.data) || typeof truncated !== "boolean") {
    return null;
  }
  const sources = Array.isArray(output.sources)
    ? output.sources.filter((item): item is string => typeof item === "string" && item !== "")
    : [];
  const fetchedAt = typeof output.fetched_at === "string" ? output.fetched_at : null;
  return { content, truncated, sources, fetchedAt };
}

// 링크로 만들어도 되는 주소인지. http·https 절대 주소만 받고(javascript:, data:, 상대 주소는 null),
// 브라우저가 해석한 주소(href)를 돌려준다.
export function safeHttpUrl(value: unknown): string | null {
  if (typeof value !== "string") return null;
  try {
    const url = new URL(value);
    return url.protocol === "http:" || url.protocol === "https:" ? url.href : null;
  } catch {
    return null;
  }
}
