"""붙여 넣은 영화 시간표를 회차 목록으로 읽고, 모델이 옮긴 회차가 실제로 그 안에 있는지 확인한다(영화 비서 C 보강).

진입점: app/tools/showtimes.py compare_showtimes_result(경로 조회 전에 부른다). 순수 함수만 있다(DB·외부 호출 없음).
근거: 2026-11-19 사용자 지시 "사용자가 실제로 제공한 극장·영화·시각의 조합만 추천에 사용,
확정할 수 없으면 무엇이 모호한지 짧게 알린다".
예전 검사(시각·상영 시간 숫자가 글 어딘가에 있는가)는 다른 극장 줄의 시각, '19:30' 안의 '9:30',
다른 회차의 상영 시간, 식사·귀가 문장의 숫자를 막지 못했다(tests/test_showtime_paste.py).

읽는 방법(줄 단위, 메시지마다 문맥을 새로 시작):
- 이름: 극장·영화 이름은 모델이 넘긴 이름을 공백 무시로 찾되 앞뒤에 글자·숫자가 붙어 있으면 다른 이름으로 본다
  ("CGV 강남" ≠ "CGV 강남역", "영화 A" ≠ "영화 A2").
  모르는 극장 브랜드(CGV·롯데시네마·메가박스 등)도 '다른 극장'으로 센다.
- 시각: 'HH:MM'만 시각으로 본다(앞뒤 숫자·콜론 경계). '오후 7:30'은 19:30, 앞에 오전·오후 없는 '7:30'은 07:30이며
  19:30 회차의 근거가 되지 않는다(오전인지 오후인지 묻는다). '19:55~21:35'의 뒤쪽은 그 회차의 종료다.
- 목록 줄과 문장 구분: 이름·시각·상영 시간·흔한 표기(관·석·자막·IMAX 등)를 빼고 남는 글자가 NOISE_LIMIT 이하인 줄만
  시간표 줄로 본다. "23:30까지 집에 도착해야 해" 같은 문장 속 시각·'밥 60분'은 회차가 되지 않는다.
- 연결: 시각은 같은 줄에서 앞에 나온 극장·영화 이름에 붙고, 줄에 없으면 위쪽 머리글(극장 줄 → 영화 줄)을 따른다.
  새 극장 머리글은 영화 문맥을 지우고, 이름을 모르는 머리글(다른 영화)도 영화 문맥을 지운다(앞 영화를 물려받지 않게).
  상영 시간은 그 시각 뒤 같은 줄(다음 시각 전)에서, 없으면 그 영화 머리글 줄에서 하나만 찾는다.

지원하지 않는 형식(확인 질문으로 돌아간다): 'HH시 MM분' 같은 글 시각, 극장·영화가 없는 시각, 오전·오후 없는 12시간제,
한 회차에 상영 시간이 여럿 적힌 줄, 극장 이름 뒤에 조사가 붙은 경우("CGV 강남에서").

고칠 때 주의: 거절 문구의 시각 표기(HH:MM)와 "오전인지 오후인지"·"극장·영화"·"상영 시간"·"종료" 낱말은
tests/test_showtime_paste.py가 찾는다. NOISE_LIMIT·FILLER를 바꾸면 문장 숫자 테스트(test_6_*)를 다시 본다.

변경 이력:
- 2026-11-19 처음 작성.
"""

import re
from dataclasses import dataclass
from datetime import datetime

EDGE = "0-9A-Za-z가-힣"
TIME = re.compile(r"(?<![\d:])(?:(오전|오후|AM|PM)\s*)?(\d{1,2}):([0-5]\d)(?![\d:])", re.IGNORECASE)
RANGE_GAP = re.compile(r"\s*[~\-–]\s*")
RUNTIME = re.compile(r"(?<!\d)(?:(\d)\s*시간\s*)?(\d{1,3})\s*분")
BRAND = re.compile(rf"(?:CGV|롯데시네마|메가박스|씨네Q|씨네큐)[ ]?[{EDGE}]*", re.IGNORECASE)
# 시간표 줄에 흔한 표기. 지워도 남는 글자가 NOISE_LIMIT를 넘으면 문장으로 본다(문장 속 숫자를 회차로 쓰지 않게).
FILLER = re.compile(
    r"상영|러닝타임|시간|회차|관람가?|잔여|좌석|매진|조조|심야|자막|더빙|일반|IMAX|4DX|SCREENX|돌비|Dolby|ATMOS|"
    r"리클라이너|컴포트|부터|시작|종료|\d+\s*관|\d+\s*석|관|석|분|회",
    re.IGNORECASE,
)
NOISE_LIMIT = 4
DAY = 24 * 60


@dataclass(frozen=True)
class Mention:
    kind: str  # "theater" | "movie"
    key: str | None  # 모델이 넘긴 이름의 정규화 값. 모르는 극장·머리글이면 None
    text: str
    start: int
    end: int


@dataclass(frozen=True)
class Slot:
    """시간표에서 읽은 회차 하나. theater·movie가 None이면 그 시각이 어느 극장·영화 것인지 글에서 알 수 없다."""

    start: int  # 그날 0시부터 분(24시 이후 표기는 1440 이상)
    raw: str
    bare: bool  # 오전·오후 없이 12 이하 시
    theater: Mention | None
    movie: Mention | None
    end: int | None
    runtime: int | None
    runtime_many: bool


@dataclass
class Context:
    theater: Mention | None = None
    movie: Mention | None = None
    movie_runtime: list[int] | None = None


def norm(name: str) -> str:
    return re.sub(r"\s+", "", name).lower()


def name_pattern(name: str) -> re.Pattern[str]:
    """공백은 무시하고, 앞뒤에 글자·숫자가 이어지면 다른 이름으로 보는 패턴."""
    body = r"\s*".join(re.escape(c) for c in name if not c.isspace())
    return re.compile(rf"(?<![{EDGE}]){body}(?![{EDGE}])", re.IGNORECASE)


def find_mentions(line: str, theaters: list[str], movies: list[str]) -> list[Mention]:
    """줄 안의 극장·영화 이름(긴 것 우선, 겹치면 하나만)과 모르는 극장 브랜드."""
    found = [
        Mention(kind, norm(name), m.group(), m.start(), m.end())
        for kind, names in (("theater", theaters), ("movie", movies))
        for name in sorted(set(names), key=len, reverse=True)
        for m in name_pattern(name).finditer(line)
    ]
    found += [Mention("theater", None, m.group().strip(), m.start(), m.end()) for m in BRAND.finditer(line)]
    kept: list[Mention] = []
    for item in sorted(found, key=lambda x: (x.start, -(x.end - x.start), x.key is None)):
        if all(item.end <= k.start or item.start >= k.end for k in kept):
            kept.append(item)
    return kept


def minutes_of(match: re.Match[str]) -> tuple[int, bool]:
    """(분, 오전·오후 없는 12시 이하인가). 24시 이후 표기(25:10)는 1440 이상 그대로."""
    meridiem, hour, minute = (match.group(1) or "").upper(), int(match.group(2)), int(match.group(3))
    if meridiem in ("오후", "PM") and hour < 12:
        hour += 12
    if meridiem in ("오전", "AM") and hour == 12:
        hour = 0
    return hour * 60 + minute, not meridiem and hour <= 12


def runtimes_in(text: str) -> list[int]:
    return [int(h or 0) * 60 + int(m) for h, m in RUNTIME.findall(text)]


def is_listing(line: str, mentions: list[Mention]) -> bool:
    """이름·시각·상영 시간·흔한 표기를 빼고 남는 글자가 NOISE_LIMIT 이하면 시간표 줄."""
    rest = line
    for m in sorted(mentions, key=lambda x: x.start, reverse=True):
        rest = rest[: m.start] + " " + rest[m.end :]
    rest = FILLER.sub(" ", RUNTIME.sub(" ", TIME.sub(" ", rest)))
    return len(re.findall(r"[A-Za-z가-힣]", rest)) <= NOISE_LIMIT


def read_header(line: str, mentions: list[Mention], ctx: Context) -> None:
    """시각 없는 줄: 극장 머리글이면 극장 문맥을 바꾸고 영화 문맥을 지운다. 영화 머리글이면 영화 문맥.
    이름을 모르는 글 줄도 영화 문맥을 지운다(다른 영화의 머리글일 수 있어 앞 영화를 물려받지 않게)."""
    if not line.strip():
        return
    theaters = [m for m in mentions if m.kind == "theater"]
    movies = [m for m in mentions if m.kind == "movie" and (not theaters or m.start > theaters[-1].start)]
    listing = is_listing(line, mentions)
    if theaters:
        ctx.theater = theaters[-1]
        ctx.movie, ctx.movie_runtime = None, None
    if movies:
        ctx.movie, ctx.movie_runtime = movies[-1], (runtimes_in(line) if listing else None)
    elif not theaters:
        ctx.movie, ctx.movie_runtime = Mention("movie", None, line.strip()[:30], 0, 0), None


def starts_and_ends(line: str) -> list[tuple[re.Match[str], re.Match[str] | None]]:
    """(시작 시각, 바로 뒤 '~'로 이어진 종료 시각) 목록. 종료로 쓰인 시각은 시작으로 세지 않는다."""
    matches = list(TIME.finditer(line))
    pairs, used = [], set()
    for i, m in enumerate(matches):
        if i in used:
            continue
        nxt = matches[i + 1] if i + 1 < len(matches) else None
        joined = nxt is not None and RANGE_GAP.fullmatch(line[m.end() : nxt.start()]) is not None
        if joined:
            used.add(i + 1)
        pairs.append((m, nxt if joined else None))
    return pairs


def bound(mentions: list[Mention], kind: str, pos: int) -> Mention | None:
    """같은 줄에서 pos 앞의 마지막 이름, 없으면 줄의 첫 이름."""
    same = [m for m in mentions if m.kind == kind]
    before = [m for m in same if m.start < pos]
    return before[-1] if before else (same[0] if same else None)


def slot_movie(mentions: list[Mention], pos: int, theater: Mention | None, ctx: Context) -> Mention | None:
    """줄에 영화가 있으면 그것. 없으면 위 머리글의 영화(같은 극장일 때만 — 다른 극장의 영화를 물려받지 않게)."""
    movie = bound(mentions, "movie", pos)
    if movie is not None:
        return movie
    line_theater = bound(mentions, "theater", pos)
    if line_theater is not None and (ctx.theater is None or line_theater.key != ctx.theater.key):
        return None
    return ctx.movie


def slot_runtime(line: str, pos: int, next_pos: int, only_one: bool, from_header: list[int] | None) -> list[int]:
    """그 시각 뒤 같은 줄(다음 시각 전)의 상영 시간. 없으면 줄 전체(시각이 하나뿐일 때), 그다음 영화 머리글 줄."""
    found = runtimes_in(line[pos:next_pos])
    if not found and only_one:
        found = runtimes_in(line)
    return found or list(from_header or [])


def read_times(line: str, mentions: list[Mention], ctx: Context) -> list[Slot]:
    """시간표 줄의 회차들. 줄에 나온 극장·영화가 있으면 문맥을 그것으로 바꾼다."""
    pairs = starts_and_ends(line)
    slots = []
    for i, (start, end) in enumerate(pairs):
        theater = bound(mentions, "theater", start.start()) or ctx.theater
        movie = slot_movie(mentions, start.start(), theater, ctx)
        next_pos = pairs[i + 1][0].start() if i + 1 < len(pairs) else len(line)
        header = ctx.movie_runtime if movie is ctx.movie else None
        runtimes = slot_runtime(line, (end or start).end(), next_pos, len(pairs) == 1, header)
        value, bare = minutes_of(start)
        end_value = minutes_of(end)[0] if end else None
        one = runtimes[0] if len(runtimes) == 1 else None
        slots.append(Slot(value, start.group().strip(), bare, theater, movie, end_value, one, len(runtimes) > 1))
    if theaters := [m for m in mentions if m.kind == "theater"]:
        ctx.theater, ctx.movie, ctx.movie_runtime = theaters[-1], None, None
    if movies := [m for m in mentions if m.kind == "movie"]:
        ctx.movie, ctx.movie_runtime = movies[-1], None
    return slots


def parse(messages: list[str], theaters: list[str], movies: list[str]) -> list[Slot]:
    """메시지들의 회차 목록. 메시지마다 극장·영화 문맥을 새로 시작한다(다른 메시지의 머리글을 물려받지 않게)."""
    slots: list[Slot] = []
    for message in messages:
        ctx = Context()
        for line in message.splitlines():
            mentions = find_mentions(line, theaters, movies)
            if not TIME.search(line):
                read_header(line, mentions, ctx)
            elif is_listing(line, mentions):
                slots += read_times(line, mentions, ctx)
    return slots


def describe(slot: Slot) -> str:
    """'20:10은 메가박스 코엑스 영화 B 회차'. 이름을 모르는 영화는 빼고 극장까지만 말한다."""
    names = [slot.theater.text if slot.theater else ""]
    if slot.movie is not None and slot.movie.key is not None:
        names.append(slot.movie.text)
    return f"{slot.raw}은 {' '.join(n for n in names if n)} 회차"


def matches(slot: Slot, theater: str, movie: str) -> bool:
    return (
        slot.theater is not None
        and slot.theater.key == norm(theater)
        and slot.movie is not None
        and slot.movie.key == norm(movie)
    )


def time_problem(slots: list[Slot], theater: str, movie: str, start: datetime) -> tuple[list[Slot], str | None]:
    """(같은 극장·영화·시각의 회차들, 없을 때 이유)."""
    label, target = f"{theater} {movie} {start:%H:%M}", start.hour * 60 + start.minute
    same_time = [s for s in slots if s.start % DAY == target]
    if not same_time:
        bare = [s for s in slots if s.bare and (s.start + 12 * 60) % DAY == target]
        if bare:
            return [], f"'{bare[0].raw}'이 오전인지 오후인지 시간표만으로 알 수 없습니다"
        return [], f"'{label}' 회차가 시간표에 없습니다"
    mine = [s for s in same_time if matches(s, theater, movie)]
    if mine:
        return mine, None
    # 모호함: 극장을 알 수 없거나, 같은 극장인데 영화를 알 수 없다. 다른 극장 줄이면 모호함이 아니라 다른 회차다.
    loose = [s for s in same_time if s.theater is None or (s.theater.key == norm(theater) and s.movie is None)]
    if loose:
        return [], f"'{loose[0].raw}'이 어느 극장·영화 회차인지 시간표에서 확정할 수 없습니다"
    return [], f"'{label}' 회차가 시간표에 없습니다({describe(same_time[0])})"


def showing_problem(
    slots: list[Slot], theater: str, movie: str, start: datetime, end: datetime | None, runtime: int | None
) -> str | None:
    """모델이 옮긴 회차가 시간표의 한 회차와 극장·영화·시작·종료·상영 시간까지 맞으면 None, 아니면 짧은 이유."""
    mine, problem = time_problem(slots, theater, movie, start)
    if problem:
        return problem
    end_value = end.hour * 60 + end.minute if end else None
    for slot in mine:
        end_ok = end_value is None or (slot.end is not None and slot.end % DAY == end_value)
        if end_ok and (runtime is None or slot.runtime == runtime):
            return None
    label, slot = f"{theater} {movie} {start:%H:%M}", mine[0]
    if end is not None and (slot.end is None or slot.end % DAY != end_value):
        return f"{label} 회차의 종료 {end:%H:%M}가 시간표의 그 회차에 없습니다"
    if slot.runtime_many:
        return f"{label} 회차의 상영 시간이 여러 개 적혀 있어 확정할 수 없습니다"
    return f"{label} 회차의 상영 시간 {runtime}분이 시간표의 그 회차에 없습니다"
