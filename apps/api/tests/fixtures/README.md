# 외부 API 응답 fixture

테스트는 네트워크에 나가지 않고 이 파일들을 가짜 HTTP(`tests/fake_http.py`)로 돌려준다.
키, 토큰, 개인정보는 들어 있지 않다(가게 전화번호는 카카오맵에 공개된 업체 번호다).

## 실제 응답 (2026-11-17, 이 PC에서 도구당 1~2회 호출해 저장)

| 파일 | 요청 |
| --- | --- |
| `open_meteo_forecast.json` | Open-Meteo forecast, 위도 37.5446 경도 127.0559, 2026-11-20, timezone Asia/Seoul |
| `kakao_locate_seongsu.json` | 카카오 키워드 검색 `성수동`, size 1 (좌표 변환) |
| `kakao_keyword_seongsu_food.json` | 카카오 키워드 검색 `맛집`, 위 좌표 반경 2000m, size 5 |
| `naver_webkr_seongsu_food.json` | NAVER API HUB 웹문서 검색 `성수동 맛집`, display 5 |

## 실제 응답이 아님 (공식 문서의 형식대로 만든 것)

실패 응답은 일부러 만들 수 없어서, Google 응답은 실제 계정을 연결할 수 없어서 문서 형식으로 만들었다.
실제 응답과 칸 이름이나 문구가 다를 수 있다. 실제로 받아 보면 바꿔 넣는다.

| 파일 | 근거 |
| --- | --- |
| `kakao_error_401.json`, `kakao_error_quota.json` | 카카오 공통 에러 코드 표(-401 → 401, -10 → 400). 본문 모양(msg, code)은 문서에 예시가 없어 추정 |
| `kakao_keyword_empty.json` | 키워드 검색 응답 형식에서 documents만 비움 |
| `open_meteo_error_429.json`, `open_meteo_empty.json` | Open-Meteo 오류 형식 `{"error": true, "reason": ...}`, 빈 daily |
| `naver_error_401.json` | NAVER API HUB 개요 문서의 API Gateway 오류 예시 |
| `naver_error_429.json` | 같은 Gateway 오류 모양에 429 문구를 넣음(문서에 429 본문 예시 없음) |
| `naver_webkr_empty.json` | 웹문서 검색 응답 형식에서 items만 비움 |
| `google_token_*.json` | OAuth 2.0 웹 서버 앱 문서의 토큰 교환·갱신 응답, `invalid_grant` 오류 |
| `google_events_*.json`, `google_event_*.json` | Calendar API Events 리소스 형식. 세 번째 일정은 주입 문장을 넣은 시험용 |
| `google_error_*.json` | Calendar API 오류 처리 문서의 형식(authError, rateLimitExceeded, userRateLimitExceeded, notFound) |
