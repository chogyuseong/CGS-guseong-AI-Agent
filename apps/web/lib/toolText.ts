// 도구 입력·결과를 사람이 읽는 글자로 바꾸는 함수 모음(승인 카드와 작업 단계 타임라인이 함께 쓴다).
// 값은 모두 글자로만 돌려준다. 화면은 이 글자를 React 텍스트로만 그린다(HTML로 해석하지 않는다).
//
// 쓰는 곳: components/ApprovalCard.tsx(승인 카드의 도구 이름·인자), ExternalContent.tsx(외부 콘텐츠 칸),
// AutomationItems.tsx(감시 도구 이름). 값은 GET /api/tasks/{id}의 approvals·steps, /api/watches에서 온다.
//
// 고칠 때 주의:
// - FIELD_LABELS·TOOL_NAMES의 키는 서버 도구 이름(apps/api/app/tools/*.py의 함수 이름)과 인자·결과 칸 이름이다.
//   도구를 더하거나 칸 이름을 바꾸면 여기에 한국어 이름을 더한다(빠져도 원래 이름이 보일 뿐 깨지지는 않는다).
// - 시각 해석은 서울 기준이다. 도구 입력의 시간대 없는 시각(LOCAL_TIME)은 서울 벽시계로 보고 바꾸지 않는다.
// - 테스트: lib/toolText.test.ts.

// 도구 입력·결과의 칸 이름 → 한국어. 없는 이름은 원래 이름을 그대로 보여 준다.
const FIELD_LABELS: Record<string, string> = {
  title: "제목",
  start: "시작",
  end: "끝",
  location: "장소",
  description: "메모",
  event_id: "일정 ID",
  id: "ID",
  all_day: "종일",
  date_from: "시작 날짜",
  date_to: "끝 날짜",
  events: "일정",
  query: "검색어",
  near: "근처",
  radius_m: "반경(m)",
  size: "개수",
  places: "장소",
  items: "검색 결과",
  message: "안내",
  name: "이름",
  address: "주소",
  category: "분류",
  phone: "전화",
  place_url: "링크",
  link: "링크",
  latitude: "위도",
  longitude: "경도",
  distance_m: "거리(m)",
  summary: "요약",
  place: "장소",
  matched_place: "좌표를 잡은 장소",
  date: "날짜",
  min_c: "최저 기온(°C)",
  max_c: "최고 기온(°C)",
  precipitation_probability_max: "최대 강수 확률(%)",
  hourly: "시간대별",
  time: "시각",
  temp_c: "기온(°C)",
  precipitation_probability: "강수 확률(%)",
  weather: "날씨",
  saved_to: "저장한 곳",
  deleted_id: "지운 일정 ID",
  deleted_from: "지운 곳",
  // 7단계 메일 도구(list_mail, read_mail)
  message_id: "메일 ID",
  max_results: "개수",
  label: "라벨",
  messages: "메일",
  from: "보낸 사람",
  subject: "제목",
  received_at: "받은 시각",
  unread: "안 읽음",
  snippet: "미리보기",
  body: "본문",
  recipient: "받는 사람",
  body_truncated: "본문 잘림",
  body_note: "본문 안내",
  attachments: "첨부(이름만)",
  attachment_count: "첨부 수",
};

const TOOL_NAMES: Record<string, string> = {
  create_event: "캘린더 일정 만들기",
  update_event: "캘린더 일정 고치기",
  delete_event: "캘린더 일정 지우기",
  list_events: "캘린더 일정 보기",
  search_places: "장소 찾기",
  web_search: "웹 검색",
  compare_products: "쿠팡 상품 비교",
  plan_departure: "출발 시각 계산",
  compare_showtimes: "영화 회차 비교",
  list_now_showing: "상영작 확인",
  recommend_movies: "영화 추천",
  list_experiences: "내 경험 읽기",
  show_job_prep: "지원 준비 카드",
  coupang_price: "쿠팡 목표가 확인",
  get_weather: "날씨 보기",
  list_mail: "메일 찾기",
  read_mail: "메일 읽기",
  draft_message: "메시지 초안 쓰기",
};

// Object.hasOwn: "toString" 같은 객체 기본 속성을 칸 이름으로 착각하지 않게 한다.
function ownValue(table: Record<string, string>, key: string): string | undefined {
  return Object.hasOwn(table, key) ? table[key] : undefined;
}

export function fieldLabel(key: string): string {
  return ownValue(FIELD_LABELS, key) ?? key;
}

export function toolDisplayName(toolName: string): string {
  const name = ownValue(TOOL_NAMES, toolName);
  return name === undefined ? toolName : `${name} (${toolName})`;
}

const WEEKDAYS = "일월화수목금토";
// 도구 입력 형식: 서울 기준 YYYY-MM-DDTHH:MM(초는 있어도 됨), 시간대 없음.
const LOCAL_TIME = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::\d{2})?$/;
// 시간대가 붙은 ISO 시각(일정 조회 결과 등).
const ZONED_TIME = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})$/;

// 날짜 숫자가 실제 날짜인지(2월 30일 등은 null) Date.UTC로 다시 만들어 확인하고 요일을 붙인다.
// UTC로 계산하는 이유: 기기 시간대와 무관하게 받은 날짜 그대로의 요일을 얻으려고.
function describeWallTime(year: number, month: number, day: number, clock: string): string | null {
  const date = new Date(Date.UTC(year, month - 1, day));
  if (date.getUTCMonth() !== month - 1 || date.getUTCDate() !== day) return null;
  return `${year}년 ${month}월 ${day}일(${WEEKDAYS[date.getUTCDay()]}) ${clock}`;
}

const SEOUL_PARTS = new Intl.DateTimeFormat("en-CA", {
  timeZone: "Asia/Seoul",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
});

function seoulWallTime(value: string): string | null {
  const instant = new Date(value);
  if (Number.isNaN(instant.getTime())) return null;
  const parts = Object.fromEntries(
    SEOUL_PARTS.formatToParts(instant).map((part) => [part.type, part.value]),
  );
  const clock = `${parts.hour}:${parts.minute}`;
  return describeWallTime(Number(parts.year), Number(parts.month), Number(parts.day), clock);
}

// 시각 글자를 "2026년 11월 20일(금) 19:00"으로. 시각 모양이 아니면 null(원래 글자를 쓴다).
export function formatDateTimeText(value: string): string | null {
  const local = LOCAL_TIME.exec(value);
  if (local) {
    const [, year, month, day, hour, minute] = local;
    return describeWallTime(Number(year), Number(month), Number(day), `${hour}:${minute}`);
  }
  return ZONED_TIME.test(value) ? seoulWallTime(value) : null;
}

// 칸 하나의 값을 글자로. 객체·배열은 JSON 글자로 보여 준다(외부 글이어도 텍스트로만 그려지므로 안전하다).
export function formatFieldValue(value: unknown): string {
  if (value === null || value === undefined) return "없음";
  if (typeof value === "string") return formatDateTimeText(value) ?? value;
  if (typeof value === "boolean") return value ? "예" : "아니요";
  if (typeof value === "number") return String(value);
  return JSON.stringify(value);
}
