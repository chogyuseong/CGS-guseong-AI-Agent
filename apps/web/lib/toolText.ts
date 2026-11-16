// 도구 입력·결과를 사람이 읽는 글자로 바꾸는 함수 모음(승인 카드와 작업 단계 타임라인이 함께 쓴다).
// 값은 모두 글자로만 돌려준다. 화면은 이 글자를 React 텍스트로만 그린다(HTML로 해석하지 않는다).

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
};

const TOOL_NAMES: Record<string, string> = {
  create_event: "캘린더 일정 만들기",
  update_event: "캘린더 일정 고치기",
  delete_event: "캘린더 일정 지우기",
  list_events: "캘린더 일정 보기",
  search_places: "장소 찾기",
  web_search: "웹 검색",
  get_weather: "날씨 보기",
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

// 칸 하나의 값을 글자로.
export function formatFieldValue(value: unknown): string {
  if (value === null || value === undefined) return "없음";
  if (typeof value === "string") return formatDateTimeText(value) ?? value;
  if (typeof value === "boolean") return value ? "예" : "아니요";
  if (typeof value === "number") return String(value);
  return JSON.stringify(value);
}
