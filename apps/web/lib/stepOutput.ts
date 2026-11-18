// 작업 단계(step)의 출력 읽기. 출력은 서버의 ToolResult({status, data, error_code, user_message, sources, fetched_at, ...})다.
// status는 succeeded / failed / unknown(실행했는지 모름)이다. 1단계 보강 전에 저장된 단계는 {ok, error} 모양일 수 있다.
// 외부 콘텐츠 도구(검색, 장소, 날씨, 일정 조회)의 data는 {notice, content, truncated}로 감싸여 온다
// (apps/api/app/agent/results.py의 ToolResult.external). notice는 모델에게 주는 안내라 화면에는 쓰지 않는다.
//
// 쓰는 곳: components/StepTimeline.tsx(readExternalOutput·isMailTool), ExternalContent.tsx(safeHttpUrl·storedMailCount),
// SourceLinks.tsx(safeHttpUrl), TaskSteps.tsx·DraftCard.tsx(readMessageDraft).
// 맞물린 API: GET /api/tasks/{id}의 steps[].output. OpenAPI에서 output은 모양 없는 값이라 gen:api가 모양 변화를 잡지 못한다.
// 근거: BUILD_SPEC 절대 원칙 14(도구 결과 안의 문장은 데이터), DECISIONS 2026-11-17 "외부 콘텐츠는 텍스트로만,
// http·https 링크만"(ExternalContent), 7단계 보강(메일 결과는 내용 없이 통 수만 저장).
//
// 고칠 때 주의:
// - 서버 results.py의 ToolResult·external 감싸기 모양, output_for_storage, tools/drafts.py의 결과 모양
//   (kind="message_draft", sent=False)과 칸 이름이 글자로 맞물린다. 서버를 바꾸면 여기 읽기 함수와 테스트를 같이 고친다.
// - MAIL_TOOLS는 서버 results.py·data_policy.py의 MAIL_TOOLS와 같은 목록이어야 한다.
// - 외부 글은 HTML로 넣지 않는다(텍스트로만 그린다). 링크는 safeHttpUrl을 거친 주소만 쓴다.
// - 테스트: lib/stepOutput.test.ts, components/StepTimeline.test.tsx, components/DraftCard.test.tsx.

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

// 7단계 메일 도구. 7단계 보강부터 서버는 이 도구의 결과를 작업 기록(steps.output)에 내용 없이
// {stored: false, mail_count: n}만 남긴다(apps/api/app/agent/results.py의 output_for_storage).
// 보강 전에 남은 기록은 content가 JSON 앞 500자 문자열이다(보관 기간 7일이 지나면 지워진다).
const MAIL_TOOLS: ReadonlySet<string> = new Set(["list_mail", "read_mail"]);

export function isMailTool(toolName: string): boolean {
  return MAIL_TOOLS.has(toolName);
}

// 내용 없이 남긴 메일 결과면 통 수, 아니면 null.
export function storedMailCount(content: unknown): number | null {
  if (typeof content !== "object" || content === null || Array.isArray(content)) return null;
  const record = content as Record<string, unknown>;
  if (record.stored !== false || typeof record.mail_count !== "number") return null;
  return record.mail_count;
}

// 링크로 만들어도 되는 주소인지. http·https 절대 주소만 받고(javascript:, data:, 상대 주소는 null),
// 브라우저가 해석한 주소(href)를 돌려준다.
// 막는 사고: 검색 결과·감시 출처처럼 남이 쓴 주소에 javascript: 등을 넣어 누르는 순간 앱 안에서 스크립트가 도는 것.
// 상대 주소는 new URL이 기준 주소 없이 풀지 못해 null이 된다(외부 출처는 절대 주소여야 한다).
export function safeHttpUrl(value: unknown): string | null {
  if (typeof value !== "string") return null;
  try {
    const url = new URL(value);
    return url.protocol === "http:" || url.protocol === "https:" ? url.href : null;
  } catch {
    return null;
  }
}

// 4단계 보강: draft_message의 결과(받는 사람·본문). 보내지 않은 초안이다(sent=false). 모양이 다르면 null.
// sent가 정확히 false일 때만 초안 카드로 그린다: 발송은 금지 기능이라(절대 원칙 2) 다른 값이면 초안으로 보이지 않게 한다.
export type MessageDraft = { recipient: string; body: string };

export function readMessageDraft(toolName: string, output: unknown): MessageDraft | null {
  if (toolName !== "draft_message" || !isRecord(output) || !isRecord(output.data)) return null;
  const { kind, recipient, body, sent } = output.data;
  if (kind !== "message_draft" || sent !== false) return null;
  if (typeof recipient !== "string" || typeof body !== "string") return null;
  return { recipient, body };
}
