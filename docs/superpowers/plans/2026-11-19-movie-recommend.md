# 영화 추천(영화 비서 C 둘째 단계) 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 채팅에서 "뭐 볼까?"라고 하면 지금 극장 상영작(KOBIS 상위 10편) 중 1~3편을 이유와 함께 추천하는 카드를 보여 주고, 회차 고르기(compare_showtimes)로 이어 준다.

**Architecture:** READ 도구 2개. `list_now_showing`이 KOBIS 박스오피스·상세로 사실을, TMDB로 줄거리·평점을 모아 외부 콘텐츠로 돌려준다. `recommend_movies`는 모델이 고른 KOBIS 코드와 이유만 받아, 이번 작업의 마지막 `list_now_showing` 단계 기록에 있는 영화인지 검사하고 사실 칸은 그 기록에서 채운다.

**Tech Stack:** FastAPI·Pydantic AI(도구), httpx(`app/tools/http.py`), pytest(+`fake_http`), Next.js·Vitest·Playwright.

**Spec:** `docs/superpowers/specs/2026-11-19-movie-recommend-design.md`

## Global Constraints
- AGENTS.md 도구 규약 1~9, 주석 규칙(파일 머리·함수 docstring·고칠 때 주의), 함수 40줄 이하, 이름 영어·문구 한국어.
- Python 도구는 `uv run python -m pytest`로 실행(apps/api). pytest를 두 개 동시에 돌리지 않는다.
- 키: `kobis_api_key`, `tmdb_read_token`(SecretStr). 값을 출력·로그·커밋하지 않는다.
- 사실 칸(장르·상영 시간·관람 등급·개봉일·누적 관객·감독·배우)은 KOBIS만. TMDB는 줄거리·평점·평가 수만. KOBIS가 비면 "정보 없음".
- 줄거리는 150자로 자른다(10편 결과 JSON이 `MAX_EXTERNAL_CHARS` 6,000자 안에 들어야 `recommend_movies`가 구조로 읽을 수 있다).
- 기준일: 서울 어제(`clock.now_utc()`), 목록이 비면 그 전날 한 번. TMDB 짝은 확실할 때만(정규화 제목 일치, 검색이면 개봉 연도 1년 이내 하나뿐).
- TMDB 표기 문구(카드): "이 서비스는 TMDB API를 사용하지만 TMDB의 보증이나 인증을 받지 않았습니다." + TMDB 로고(작게).
- 도구 둘 다 채팅 전용(`travel_tools`), 예약·감시에 넣지 않는다.

## Review Focus
1. 새벽(어제 박스오피스가 아직 비어 있음) → 그 전날 목록으로 조회, 카드에 실제 기준일 표시. → Task 1 테스트 `test_falls_back_to_previous_day`.
2. 같은 제목의 다른 영화(리메이크·동명) → TMDB 검색 후보 둘 이상이면 줄거리를 붙이지 않음. → Task 2 `test_search_ambiguous_skips`.
3. 상영작을 조회하지 않고 바로 추천 → "먼저 상영작을 조회" 실패. 조회가 실패(failed)한 단계만 있을 때도 같다. → Task 4 `test_requires_successful_lookup`.
4. 10편 모두 긴 줄거리·배우 이름 → 결과가 잘리지 않음(truncated False). → Task 3 `test_ten_movies_fit_external_limit`.
5. TMDB 토큰 없음·TMDB 401 → KOBIS 정보만으로 성공, 카드에 TMDB 표기 없음. → Task 3 `test_tmdb_failure_keeps_kobis`.

---

### Task 1: 설정과 KOBIS 조회

**Files:**
- Modify: `apps/api/app/config.py`(tmap_app_key 아래), `apps/api/tests/conftest.py`(`configure_external_keys`에 `test-kobis-key`, `test-tmdb-token`)
- Create: `apps/api/app/tools/kobis.py`, `apps/api/tests/fixtures/kobis_daily_boxoffice.json`, `kobis_movie_info.json`(2026-11-19 실제 응답을 3편으로 줄임, 원본은 scratchpad `kobis/box.json`·`info.json`), `tests/fixtures/README.md` 줄 추가
- Test: `apps/api/tests/test_kobis.py`

**Interfaces:**
- Produces: `@dataclass(frozen=True) class KobisMovie: code: str; title: str; open_date: str; rank: int; audience_total: int; screens: int; genres: list[str]; runtime_minutes: int | None; grade: str | None; directors: list[str]; actors: list[str]; production_year: str | None`
- Produces: `async def fetch_now_showing(deps: AgentDeps) -> tuple[date, list[KobisMovie]]` — 기준일과 상위 10편(상세 포함). 실패는 `ExternalApiError`.
- URL 상수 `BOXOFFICE_URL`, `MOVIE_INFO_URL`(설계 문서 주소), 서비스 이름 `"KOBIS"`.

- [ ] Step 1: 테스트 작성 — `test_parses_boxoffice_and_info`(fixture로 코드·제목·순위·누적 관객·장르·상영 시간 88·등급 "15세이상관람가"·감독/배우 상위 2명), `test_falls_back_to_previous_day`(어제 빈 목록 → 전날 요청, 돌려준 기준일 = 전날), `test_fault_info_is_error`(`{"faultInfo": {...}}` → ExternalApiError code `unauthorized`, 메시지에 키 값 없음), `test_info_failure_keeps_movie`(상세 하나 실패 → 그 영화는 장르 [] 등 빈 칸으로 남음), `test_timeout_is_retryable`.
- [ ] Step 2: `uv run python -m pytest tests/test_kobis.py -v` → 실패(모듈 없음).
- [ ] Step 3: `Settings.kobis_api_key`, `Settings.tmdb_read_token` 추가(주석: 출처·없으면 꺼짐). `kobis.py` 구현. faultInfo는 응답 200이어도 오류로 바꾼다. 상세는 영화마다 1회(`asyncio.gather`), 상세 실패는 그 영화 칸만 비움.
- [ ] Step 4: 같은 명령 → 통과.
- [ ] Step 5: 커밋 `영화 추천: KOBIS 박스오피스·영화 상세 조회`.

### Task 2: TMDB 짝짓기

**Files:**
- Create: `apps/api/app/tools/tmdb.py`, fixtures `tmdb_now_playing_kr.json`, `tmdb_search_movie.json`(실제 응답을 키 사용 없이 녹화·축약)
- Test: `apps/api/tests/test_tmdb.py`

**Interfaces:**
- Produces: `@dataclass(frozen=True) class TmdbInfo: tmdb_id: int; overview: str; vote_average: float; vote_count: int`
- Produces: `def normalize_title(title: str) -> str`(공백·문장부호 제거, 소문자)
- Produces: `async def match_movies(deps: AgentDeps, movies: list[KobisMovie]) -> dict[str, TmdbInfo]`(KOBIS 코드 → 정보, 짝 없는 영화는 빠짐). 토큰이 없으면 외부 요청 없이 `{}`. TMDB 오류는 `{}`로 삼키지 않고 `ExternalApiError`를 던지고, 호출 쪽(Task 3)이 KOBIS만으로 진행한다.

- [ ] Step 1: 테스트 — `test_matches_now_playing_by_title`, `test_search_fallback_with_year`(now_playing에 없고 검색 결과 하나, 연도 차 ≤1), `test_search_ambiguous_skips`(일치 후보 2개 → 없음), `test_search_year_far_skips`, `test_no_token_no_request`(fake_http 요청 0건), `test_bearer_header`(Authorization 헤더가 `Bearer test-tmdb-token`, URL에 토큰 없음).
- [ ] Step 2: 실행 → 실패.
- [ ] Step 3: 구현. now_playing `region=KR&language=ko-KR` 1~2쪽, 검색은 짝 없는 영화만.
- [ ] Step 4: 통과 확인.
- [ ] Step 5: 커밋 `영화 추천: TMDB 줄거리·평점 짝짓기`.

### Task 3: `list_now_showing` 도구와 등록

**Files:**
- Create: `apps/api/app/tools/boxoffice.py`
- Modify: `app/agent/toolsets.py`(travel_tools, TOOL_LABELS), `app/agent/data_policy.py`(TOOL_POLICIES), `app/agent/results.py`(EXTERNAL_CONTENT_TOOLS), 도구 목록 테스트(`tests/test_agent_run.py` 등 compare_showtimes를 나열하는 곳)
- Test: `apps/api/tests/test_boxoffice.py`

**Interfaces:**
- Consumes: Task 1 `fetch_now_showing`, Task 2 `match_movies`.
- Produces: `async def list_now_showing(ctx: RunContext[AgentDeps]) -> ToolResult`, `def list_now_showing_label(args) -> str` = "상영작 확인하는 중".
- Produces(결과 content 모양, Task 4·6이 읽음): `{"기준일": "2026-11-18", "영화": [{"code", "제목", "개봉일", "순위", "누적 관객", "장르", "상영 시간", "등급", "감독", "배우", "줄거리", "평점", "평가 수"}], "출처": {"KOBIS": true, "TMDB": bool}}`. 없는 값은 "정보 없음"(줄거리·평점은 키 자체를 뺀다).
- 메모리 캐시: `CACHE: dict[date, dict]`(기준일당 하나, 최근 2일만 유지). 테스트용 `clear_cache()`.

- [ ] Step 1: 테스트 — `test_no_kobis_key_guides_without_request`, `test_returns_wrapped_movies`(external notice, sources에 KOBIS·TMDB 링크), `test_tmdb_failure_keeps_kobis`(TMDB 401 → 성공, 출처 TMDB false, 줄거리 없음), `test_ten_movies_fit_external_limit`(10편·최대 길이 → truncated False), `test_overview_clipped_150`, `test_cache_avoids_second_request`, `test_kobis_failure_retryable`. 등록부 테스트(data_policy 누락 검사)는 기존 테스트가 잡는지 실행으로 확인.
- [ ] Step 2: 실행 → 실패.
- [ ] Step 3: 구현과 등록. data_policy: `public_lookup("KOBIS·TMDB", "상영작 목록·사실 칸·줄거리와 출처")`.
- [ ] Step 4: `uv run python -m pytest tests/test_boxoffice.py tests/test_data_policy.py tests/test_agent_run.py -v` → 통과.
- [ ] Step 5: 커밋 `영화 추천: 상영작 조회 도구 등록`.

### Task 4: `recommend_movies` 도구

**Files:**
- Modify: `apps/api/app/tools/boxoffice.py`, toolsets·data_policy(외부 전송 없음: `internal`이 아닌 READ 정책, 결과 등급 public)
- Test: `apps/api/tests/test_recommend_movies.py`

**Interfaces:**
- Produces: `class Pick(BaseModel): code: str(1~20자); reason: str(1~120자)`, `async def recommend_movies(ctx, picks: Annotated[list[Pick], Field(min_length=1, max_length=3)], taste: Annotated[str | None, Field(max_length=60)] = None) -> ToolResult`, 라벨 "영화 추천 정리하는 중".
- Produces: `async def last_lookup(deps: AgentDeps) -> dict | None` — 이번 task(user_id 조건)의 `tool_name == "list_now_showing"`이고 status 성공인 Step 중 order가 가장 큰 것의 `output["data"]["content"]`(구조가 dict일 때만).
- Produces(카드 content, Task 6이 읽음): `{"결론": "<첫 제목> 추천", "기준일", "추천": [{"제목", "이유", "장르", "상영 시간", "등급", "평점", "누적 관객", "감독", "배우"}], "조건": taste, "다음": "보고 싶은 영화의 시간표를 붙여 주시면 회차를 골라 드릴게요.", "예매": [CGV·롯데시네마·메가박스 공식 주소], "출처 표기": 조회에 TMDB가 쓰였으면 Global Constraints의 TMDB 표기 문구, 아니면 키 없음}`. `ToolResult.success`(외부 글을 새로 가져오지 않음; 줄거리는 카드에 넣지 않는다).

- [ ] Step 1: 테스트 — `test_requires_successful_lookup`(조회 단계 없음/실패 단계만 → failure "먼저 상영작을 조회"), `test_unknown_code_rejected`(어느 code인지 메시지에), `test_duplicate_code_rejected`, `test_facts_come_from_lookup`(인자에 없는 장르·등급이 기록 값과 같음), `test_uses_latest_lookup`(조회 두 번 → 나중 것), `test_other_users_step_ignored`.
- [ ] Step 2: 실행 → 실패.
- [ ] Step 3: 구현·등록. 단계 기록은 테스트에서 Step 행을 직접 넣어 만든다(기존 테스트의 Step 생성 방식을 따른다).
- [ ] Step 4: 통과 확인.
- [ ] Step 5: 커밋 `영화 추천: 추천 카드 도구(목록 밖 영화 거절)`.

### Task 5: 프롬프트와 전체 흐름

**Files:**
- Modify: `apps/api/app/agent/prompts.py`(compare_showtimes 줄 아래 한 줄, 머리 주석 변경 이력), 프롬프트 규칙 테스트
- Test: `apps/api/tests/test_movie_recommend_flow.py`

- [ ] Step 1: 테스트 — FunctionModel 대본(`scripted`)으로 list_now_showing → recommend_movies → 답변: step 2개 성공, 두 번째 label "영화 추천 정리하는 중", 카드의 사실이 첫 단계 기록과 같음(실제 StepTracking 기록으로 Task 4의 `last_lookup`이 동작함을 증명). 프롬프트에 `list_now_showing`·`recommend_movies` 문구가 있는지.
- [ ] Step 2: 실행 → 실패.
- [ ] Step 3: 프롬프트 한 줄: "지금 극장에서 볼 영화 추천은 list_now_showing으로 상영작을 조회한 뒤 recommend_movies로 1~3편을 고른다. 기억된 취향과 이번 조건을 이유에 쓰고, 목록에 없는 영화는 말하지 않는다. 회차는 시간표를 붙여 달라고 한다."
- [ ] Step 4: 통과 확인 후 `pnpm api:check`(ruff·pyright) 통과.
- [ ] Step 5: 커밋 `영화 추천: 프롬프트와 전체 흐름 테스트`.

### Task 6: 화면 카드

**Files:**
- Modify: `apps/web/components/ExternalContent.tsx`(`movieRecommend` 속성과 `MovieRecommendCard`), `components/StepTimeline.tsx`(CARD_TOOLS에 두 도구, recommend_movies 결과는 일반 data라 ExternalContent 경로가 아니면 같은 카드 컴포넌트를 data로 그린다), `lib/toolText.ts`
- Create: `apps/web/public/tmdb-logo.svg`(TMDB 공식 로고 페이지에서 받음, 출처 주석은 DECISIONS), `components/MovieRecommendCard.test.tsx`, `e2e/movie-recommend.spec.ts`(고정 응답, 360px)

- [ ] Step 1: Vitest — 추천 1~3편 제목·이유·사실 칸, "다음" 문구, 예매 링크 3개, TMDB 표기가 있을 때만 로고·문구, "정보 없음" 표시.
- [ ] Step 2: 실행 → 실패.
- [ ] Step 3: 구현(디자인 원칙은 기존 MovieCard와 같은 토큰).
- [ ] Step 4: apps/web의 Vitest·Biome·tsc, e2e 360px 가로 넘침 없음 → 통과.
- [ ] Step 5: 커밋 `영화 추천: 화면 카드`.

### Task 7: 실제 확인과 문서

- [ ] Step 1: 모델 없이 실제 1회 조회 — apps/api에서 작은 스크립트로 `list_now_showing`을 tool_context로 호출(실제 키), 10편·TMDB 짝 수·truncated False 확인. 키 값은 출력하지 않는다.
- [ ] Step 2: 문서 — `docs/TOOLS.md` 절, `docs/DECISIONS.md` 한 줄(접근 C, 사실 정본 KOBIS, 단계 기록 기준 검사, TMDB 이용 조건), `docs/DATA_POLICY.md`(메모리 캐시만), `배포-이어하기.txt`.
- [ ] Step 3: 전체 `pnpm api:test`(하나만), `pnpm api:check`, 웹 검사 → 결과를 그대로 기록.
- [ ] Step 4: 커밋 → push 전 키·이메일 검사 → `origin/main` push.
- [ ] Step 5: 실제 모델 확인·eval 재실행은 예상 비용을 알리고 사용자 확인 뒤에만.
