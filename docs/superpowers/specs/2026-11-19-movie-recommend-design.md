# 영화 비서 C 둘째 단계: 극장 상영작 추천 — 설계

2026-11-19. 요구사항 밖 기능(생활 비서). 사용자 결정: 범위는 "지금 극장에서 뭘 볼지"(집·OTT 추천은 제외),
데이터는 C안 KOBIS + TMDB("C로 가자"). 첫 단계(회차 고르기, compare_showtimes)는 그대로 두고 앞에 붙인다.

## 목적
"이번 주말에 뭐 볼까?", "친구랑 가볍게 볼 영화 추천해 줘"라고 하면 지금 한국 극장에서 상영 중인 영화 중
1~3편을 이유와 함께 추천하는 카드를 보여 주고, "이 영화 시간표를 붙여 주시면 회차를 골라 드릴게요"로
기존 회차 고르기로 이어 준다.

## 목표 / 하지 않을 것 / 실패로 볼 것
- 목표: 추천 영화는 실제로 조회한 상영작 목록 안에서만 나온다(서버 검사). 카드의 사실(장르·상영 시간·등급·평점·관객 수)은
  모델이 아니라 서버가 조회 결과에서 채운다. 모델은 "어느 영화를 왜"만 정한다.
- 하지 않음: OTT·집에서 볼 영화, 극장 시간표 조회(공식 API 없음, 붙여 넣기 유지), 예매, 예약·감시에서 사용(제안으로 둠),
  상영작 목록 DB 저장, 포스터 이미지.
- 실패: 목록에 없는 영화를 추천, 카드의 사실이 조회 결과와 다름, TMDB 출처 표기 누락, 키 없을 때 외부 요청.

## 접근 방법
| | A. KOBIS만 | B. TMDB만 | C. KOBIS + TMDB (채택) |
|---|---|---|---|
| 상영작 목록 | 한국 공식 | 한국 상영 반영 늦거나 빠짐 | KOBIS 기준 |
| 추천 근거 | 관객 수·장르뿐 | 줄거리·평점 | 둘 다 |
| 단점 | 이유 설명 빈약 | 사실 기준이 외국 서비스 | 키 2개, 제목 짝짓기 필요 |
채택 이유: 사용자가 "완성도"를 원함. 사실 기준은 한국 공식, 설명 근거는 줄거리·평점.

## 공식 API (2026-11-19 확인)
- KOBIS 일별 박스오피스 `GET https://www.kobis.or.kr/kobisopenapi/webservice/rest/boxoffice/searchDailyBoxOfficeList.json`
  `key`, `targetDt=YYYYMMDD`, `itemPerPage`(최대 10). 응답 `boxOfficeResult.dailyBoxOfficeList[]`:
  rank, movieCd, movieNm, openDt, audiCnt, audiAcc, scrnCnt, showCnt 등(튜토리얼 문서). 키 없이 부르면
  `faultInfo` errorCode 320010(직접 확인). 하루 호출 한도는 문서에 없음 → 키 발급 후 이용 안내에서 확인해 TOOLS.md에 적는다.
- KOBIS 영화 상세 `GET .../rest/movie/searchMovieInfo.json?key=&movieCd=` — 2026-11-19 키로 실제 조회해 확인:
  `movieInfoResult.movieInfo`의 movieNm, movieNmEn, showTm(문자열 분), prdtYear, openDt(YYYYMMDD), nations[].nationNm,
  genres[].genreNm, directors[].peopleNm, actors[].peopleNm, audits[].watchGradeNm(예: "15세이상관람가"). 박스오피스 항목 키도
  튜토리얼과 같음(rnum, rank, movieCd, movieNm, openDt "YYYY-MM-DD", audiCnt, audiAcc, scrnCnt, showCnt 등).
  감독·배우 상위 2명도 카드 사실 칸에 넣는다(추천 이유에 쓸 수 있게).
- TMDB `GET https://api.themoviedb.org/3/movie/now_playing?region=KR&language=ko-KR&page=` (Bearer 토큰).
  결과: id, title, original_title, overview, genre_ids, release_date, vote_average, vote_count. 짝을 못 찾으면 `GET /3/search/movie?query=&language=ko-KR&region=KR`.
- TMDB 이용 조건(2026-11-19 확인): 출처 표기 의무 — TMDB 로고(앱 표시보다 덜 눈에 띄게) + "This product uses the TMDB API but
  is not endorsed or certified by TMDB" 취지 문구, 캐시 6개월 이내, 상업 이용은 별도 계약, **AI 학습에 사용 금지**
  (추론 입력은 학습이 아님. Anthropic API 입력은 학습에 쓰이지 않는 설정 — 확인 결과를 DECISIONS에 남긴다).

## 설계
### 설정
- `.env`: `KOBIS_API_KEY`, `TMDB_READ_TOKEN`(SecretStr, `.env.example`에 이름만). KOBIS 키가 없으면 도구가 외부 요청 없이
  "설정에 KOBIS 키가 없어 상영작을 조회할 수 없습니다" 안내(쿠팡과 같은 방식). TMDB 토큰만 없으면 KOBIS 정보로만 추천.

### 도구 1 `list_now_showing` (READ, 외부 콘텐츠, 채팅 전용 travel_tools 묶음) — 새 파일 `app/tools/boxoffice.py`
- 인자 없음(모델이 넣을 개인 정보가 없다). 기준일은 서울 어제(`clock.now_utc()`), 목록이 비면 그 전날 한 번 더.
- KOBIS 상위 10편 → 각 영화 상세(장르·상영 시간·관람 등급) → TMDB now_playing KR 1~2쪽에서 짝짓기.
  짝짓기: 제목 정규화(공백·문장부호 제거, 소문자)가 같으면 짝. 없으면 TMDB 검색으로 제목이 같고 개봉 연도가 KOBIS openDt와
  1년 이내인 것 하나만. 후보가 둘 이상이거나 없으면 TMDB 정보 없이 둔다(틀린 줄거리보다 없는 게 낫다).
- 같은 기준일 결과는 프로세스 메모리에 캐시(재시작하면 비움, tmap.LIMITER와 같은 수준). 한 번 조회는 최대 24회 외부 호출, 완전한 결과만 캐시(구현 중 최종 검토 반영).
- 결과(`ToolResult.external`, 출처 KOBIS·TMDB 링크): 기준일, 영화별 {code(KOBIS movieCd), 제목, 개봉일, 순위, 누적 관객,
  스크린 수, 장르, 상영 시간, 관람 등급, 줄거리(200자로 자름), 평점·평가 수}. 줄거리는 외부 글 → 지시문 무시 감싸기.
- 데이터 정책: `public_lookup("KOBIS·TMDB", ...)`, 보내는 것은 날짜·영화 코드·제목뿐. 실패: KOBIS 실패면 failed(retryable),
  TMDB 실패면 TMDB 칸만 비우고 성공. 타임아웃·재시도는 http.py 규칙(조회 1회 재시도).

### 도구 2 `recommend_movies` (READ, 외부 전송 없음, 채팅 전용)
- 인자: picks 1~3개 {code, reason(모델이 쓴 추천 이유 120자 이하)}, taste(사용자가 말한 조건 요약, 60자 이하, 선택).
- 서버 검사(내 결정, 빌더 점검 반영): 기준은 **이번 작업에서 마지막으로 성공한 list_now_showing의 steps.output**이다
  (사용자가 본 목록과 같고, 재시작·복구 뒤에도 남고, 외부 요청이 없다). 그래서 steps.output에 영화 목록 전체를 남긴다
  (공개 데이터라 가리지 않음). 이번 작업에 조회 기록이 없으면 "먼저 상영작을 조회하세요"로 실패 → 모델이 list_now_showing부터.
  모든 code가 그 목록에 있어야 하고, 없으면 어느 code인지 알려 실패. 중복 code 거절. 메모리 캐시는 외부 호출을 줄이는 용도뿐.
- 결과 카드 칸: 결론(첫 추천 제목 한 줄), 추천[{제목, 이유, 장르, 상영 시간, 등급, 평점, 누적 관객}] — 사실 칸은 서버가 채움,
  다음 단계("시간표를 붙여 주시면 회차를 골라 드릴게요"), 예매 사이트 링크(기존 BOOKING_SITES), 출처 표기 문구.
- 데이터 정책: 외부 전송 없음, 결과 등급 public.

### 화면 (apps/web)
- ExternalContent에 추천 카드(MovieRecommendCard): 추천 1~3편 카드, 각 사실 칸, 이유, 다음 단계 문구, 하단에 TMDB 로고(작게)와
  표기 문구, KOBIS 출처. `toolText.ts` 문구("상영작 확인하는 중", "영화 추천 정리하는 중"), StepTimeline CARD_TOOLS 등록.
- TMDB 로고 파일은 TMDB 공식 로고 페이지에서 받아 `apps/web/public/`에 둔다.

### 프롬프트
- 한 줄 추가: "상영 중인 영화 추천은 list_now_showing → recommend_movies. 기억된 취향과 이번 조건을 이유에 반영하고,
  목록에 없는 영화는 말하지 않는다. 회차는 시간표를 붙여 달라고 한다." → **eval 재실행 필요(유료, 사용자 확인 후)**,
  extra_dataset에 추천 사례 1~2개 추가 제안.

### 테스트
- fixture(2026-11-19 실제 응답 녹화를 줄인 것, README에 표시): KOBIS 박스오피스·상세, TMDB now_playing·search. 정상, 타임아웃, 401(키 오류),
  KOBIS faultInfo, 빈 목록(전날로 재시도), TMDB 실패(KOBIS만), 짝짓기 0개·2개.
- recommend_movies: 목록 밖 code 거절, 중복 거절, 사실 칸이 캐시 값과 같음.
- FunctionModel 대본: list_now_showing → recommend_movies → step 2개·카드. 등록부 테스트(data_policy, 도구 목록) 갱신.
- 웹: 카드 Vitest, 360px e2e(고정 응답).

### 문서
- TOOLS.md 절, DECISIONS 한 줄씩, DATA_POLICY(메모리 캐시만, DB 저장 없음), 배포-이어하기.txt.

## 빌더 점검 뒤 정한 것 (2026-11-19, 내 결정 — 사용자가 고칠 수 있음)
- 사실 칸의 정본: 장르·상영 시간·관람 등급·개봉일·관객 수는 **KOBIS만**. TMDB에서는 줄거리·평점·평가 수만 가져온다.
  KOBIS 칸이 비면 "정보 없음"으로 두고 TMDB로 채우지 않는다. 그래서 TMDB 장르 목록(genre/list)은 부르지 않는다.
- 키: 2026-11-19 사용자가 `.env`에 KOBIS_API_KEY·TMDB_READ_TOKEN을 넣었고 둘 다 실제 조회 성공(KOBIS 박스오피스 10편·상세,
  TMDB movie/11 200, now_playing KR 120편 한국어 줄거리). fixture는 이 실제 응답을 줄여서 녹화한다.
  하루 한도는 두 문서 모두 명시가 없다 → 같은 기준일 메모리 캐시(완전한 결과만)로 호출을 줄이고 별도 횟수 제한은 두지 않는다.
- TMDB 이용 조건: 이 앱은 사용자 한 명의 비상업 개인 비서라 일반 라이선스 범위로 본다. 모델에 줄거리를 넣는 것은 추론이지
  학습이 아니다. 구현 순서는 KOBIS만으로 동작하는 경로를 먼저 완성하고 TMDB를 덧붙인다(TMDB 토큰이 없어도 기능이 선다).
  로고·표기 문구는 TMDB를 붙이는 단계에서 함께 넣는다.

## 빌더에게 맡기는 것
- KOBIS 상세 응답의 실제 필드 이름(키 발급 후), 제목 정규화 세부, 카드 배치, 캐시 구조, 함수 나누기.
