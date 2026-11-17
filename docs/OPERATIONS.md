# 운영 절차 (BUILD_SPEC 9단계, 2026-11-18)

배포처는 Railway(docs/DEPLOY.md, 선택 근거는 docs/DEPLOY-OPTIONS.md)를 기준으로 쓴다.
**2026-11-18 현재 실제 배포는 하지 않았다.** 아래 Railway 절차는 배포 뒤 공식 문서로 메뉴 이름·기능(특히 백업)을 다시 확인한다.
실제로 해 본 것은 맨 아래 "복원 연습 기록(로컬)"뿐이다.

상태 전이는 docs/STATE_MACHINES.md, 보관·삭제 범위는 docs/DATA_POLICY.md, 장애 결과 표는 docs/FAULTS.md,
강제 종료 복구 확인은 docs/DURABILITY-CHECK.md.

## 1. 마이그레이션

### 적용

- Railway api 서비스의 Pre-deploy Command가 `uv run python -m alembic upgrade head`다(docs/DEPLOY.md 2절).
  실패하면 새 버전이 뜨지 않고 옛 버전이 계속 돈다.
- 배포 전에 로컬에서 확인한다(apps/api):
  1. `uv run python -m alembic upgrade head` → `uv run python -m alembic downgrade -1` → `upgrade head`(업·다운·업)
  2. `uv run python -m alembic check`("No new upgrade operations detected")
  3. `pnpm api:test`
- 새 마이그레이션이 있는 배포 전에는 백업을 하나 만든다(아래 2절).
- 복제본은 1개다. 새 버전과 옛 버전이 같은 DB를 동시에 쓰는 시간이 짧도록, 마이그레이션은 옛 코드가 읽을 수 있게
  "추가 먼저, 삭제는 다음 배포에서" 순서로 만든다.

### 롤백

기본은 **코드만 되돌리고 마이그레이션은 되돌리지 않는다**(Railway Deployments에서 이전 배포로 Rollback).
마이그레이션이 칸·표를 더하기만 했다면 옛 코드는 그대로 돈다.

`alembic downgrade`가 필요한 경우(옛 코드가 새 스키마에서 돌지 못할 때)만 아래 순서로 한다.

1. 백업을 만든다(2절). 되돌리기 전 상태를 남긴다.
2. api 서비스를 멈춘다(실행 중 워크플로가 DB를 쓰지 않게).
3. api 셸에서 `uv run python -m alembic downgrade -1`(한 단계씩). `alembic current`로 확인.
4. 이전 코드로 배포(Rollback). Pre-deploy가 옛 head로 upgrade를 시도해도 이미 그 버전이라 아무것도 하지 않는다.

downgrade의 위험(이 저장소의 실제 마이그레이션에서):

- 상태 값을 늘린 마이그레이션의 downgrade는 **새 값을 옛 값으로 바꿔 쓴다**(되돌릴 수 없다). 예:
  `e99a45393f2d`는 cancel_requested→cancelled, waiting_input·partially_done→done, reconciling·needs_review→failed,
  승인 cancelled→rejected. `58052402b7da`·`fdef064b5c87`은 step unknown·expired→failed.
  "결과 불명"이 "실패"가 되므로 needs_review·unknown 작업이 있으면 downgrade 전에 사용자가 결과를 확인한다.
- 칸·표를 더한 마이그레이션의 downgrade는 그 데이터를 지운다(예: 메일 동의 시각, 실행 계획·영수증, outbox, 발생 기록).
  영수증을 지우면 "완료"의 근거가 사라진다.
- 상태 값은 PostgreSQL enum이 아니라 CHECK 제약이다. 코드에서 값을 더하면 마이그레이션도 필요하다(`alembic check`가 잡지 못하므로
  CHECK 제약을 바꾸는 마이그레이션을 직접 쓴다. 예: `58052402b7da`).

## 2. 백업과 복원

### 백업

- 앱 표와 DBOS 시스템 표(`dbos` 스키마)는 같은 DB에 있어 한 번에 백업된다.
- Railway: Postgres 서비스의 백업 기능(보관 기간·주기)을 배포 때 확인하고 docs/DATA_POLICY.md "백업과 삭제 지연"을 고친다.
  목표 보관은 30일(BUILD_SPEC 5.2).
- 수동 백업(배포 전·downgrade 전): Postgres에 접속할 수 있는 곳에서 `pg_dump -Fc`로 만든다.
  덤프 파일에는 대화·기억·암호화된 토큰이 들어 있으므로 저장소·채팅에 올리지 않고, 쓴 뒤 지운다.
  덤프만으로는 Google 토큰과 메일 기록을 풀 수 없다(키는 DB 밖, 4절). 반대로 키를 잃으면 백업이 있어도 풀 수 없다.

### 복원

1. **새 DB(또는 새 Postgres 서비스)에 먼저 복원한다.** 운영 DB를 바로 덮어쓰지 않는다.
   `createdb 새DB` → `pg_restore -d 새DB --no-owner 덤프`.
2. 확인: `DATABASE_URL`을 새 DB로 둔 셸에서 `uv run python -m alembic current`가 head인지, 주요 표의 행 수가 백업 시점과
   맞는지(아래 연습 기록의 방법), `alembic check`.
3. api 서비스를 멈추고 `DATABASE_URL`을 새 DB로 바꾼 뒤 시작한다. 시작 때 DBOS가 끝나지 않은 워크플로를 이어서 실행한다
   (같은 WORKFLOW_VERSION일 때만, 3절).
4. 복원 뒤 할 일(docs/DATA_POLICY.md):
   - 백업 시점 이후에 지운 대화·기억이 되살아났다. 그 삭제를 다시 적용한다.
   - 보관 기간 정리는 1분 검사가 매시 17분에 한다. 바로 돌리고 싶으면 다음 정각 17분까지 기다리거나 서버 로그의 "보관 기간 정리"를 확인한다.
   - 백업 뒤에 실행된 외부 작업(일정 생성 등)은 DB에 기록이 없다. Google Calendar에서 직접 확인한다.
     DB에 executing·unknown으로 남은 operation은 다시 실행되지 않고(docs/STATE_MACHINES.md 2절) needs_review로 보인다.

## 3. DBOS 코드 업그레이드와 실행 중 작업

- DBOS는 `application_version`이 같은 워크플로만 복구한다. 이 앱은 `WORKFLOW_VERSION`(apps/api/app/agent/workflows.py,
  지금 `myagent-5`)으로 고정한다. 코드 해시가 아니라서 평범한 코드 수정으로는 버전이 바뀌지 않는다.
- **올려야 할 때**: durable_step 이름·순서, 워크플로 함수의 단계 구성을 바꿀 때(AGENTS.md). 옛 기록과 새 코드의 단계가 맞지 않으면
  복구가 잘못된 결과를 재생하기 때문이다.
- 버전을 올리는 배포 순서:
  1. 끝나지 않은 작업이 없는지 본다.
     `SELECT status, count(*) FROM tasks WHERE status IN ('queued','running','cancel_requested','waiting_approval') GROUP BY 1;`
     `SELECT application_version, status, count(*) FROM dbos.workflow_status WHERE status IN ('PENDING','ENQUEUED') GROUP BY 1, 2;`
     (1분 검사 예약 워크플로는 매분 생겼다 끝나므로 잠깐 보일 수 있다.)
  2. running·cancel_requested가 있으면 끝날 때까지 기다린다(승인 대기는 워크플로가 끝난 상태라 괜찮다. 재개는 새 버전 워크플로로 시작된다).
  3. 배포한다.
  4. 배포 뒤 1의 첫 질의를 다시 본다. running으로 남은 task가 있으면 옛 버전 워크플로라 새 서버가 복구하지 않는다.
     상태를 손으로 바꾸기 전에 그 task의 operation(executing·unknown)을 보고, 외부 결과를 직접 확인한다. 지금은 이 경우를 처리하는
     명령이 없다(후속 과제).
- 버전을 안 올리는 배포(대부분): 실행 중이던 워크플로는 새 서버가 시작할 때 이어서 실행한다. 이때 도구 실행은 다시 하지 않는다
  (기록된 결과 재생, 끊긴 INTERNAL은 unknown, EXTERNAL은 재조회. docs/DURABILITY-CHECK.md).
- `PAYLOAD_ENCRYPTION_KEY`를 바꾸는 배포도 끝나지 않은 워크플로를 복구하지 못한다(4절). 같은 순서로 한다.
- 복제본은 반드시 1개다(executor "local"로 서로의 워크플로를 복구하므로 두 서버가 겹치면 같은 작업을 둘이 잡을 수 있다).

## 4. 비밀(키) 관리와 교체

값은 Railway Variables(배포)와 `.env`(로컬)에만 둔다. 저장소·채팅·로그·문서에 적지 않는다. 커밋 전에 `git diff --cached`에서
키 값·이메일을 검사한다.

| 이름 | 쓰는 곳 | 교체 방법 | 잃어버리거나 바꾸면 |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | 모델 호출 | 새 키 발급 → Variables 교체 → 재배포 → 옛 키 폐기 | 저장된 데이터 영향 없음. 키가 틀리면 모든 작업이 failed(5절) |
| `TOKEN_ENCRYPTION_KEY` | Google 토큰·OAuth 시작 정보 암호화(Fernet) | 교체 기능 없음. 바꾸면 연결 화면에서 "다시 연결 필요" → 사용자가 다시 연결 | **저장된 Google 토큰을 풀 수 없다.** 토큰을 비우고 다시 연결 필요로 표시, 그 연결의 대기 중 계획은 cancelled(connection_revoked). 백업에서 복원해도 같은 키가 없으면 못 푼다 |
| `PAYLOAD_ENCRYPTION_KEY` | DBOS 기록 전체, 모델 기록 속 메일 결과(Fernet) | 3절 순서(끝나지 않은 작업 없음 확인) 뒤 교체 | **끝나지 않은 워크플로를 복구할 수 없다**(풀지 못해 오류). 이전 대화의 메일 결과는 "다시 읽어야 한다"는 안내로 바뀐다. 비우면 메일 기능이 꺼진다. TOKEN_ENCRYPTION_KEY와 다른 값 |
| `SESSION_SECRET` | 필수 설정(지금 코드는 서버 저장 세션을 써서 읽는 곳이 없다) | 새 값으로 교체 | 영향 없음(세션은 DB `sessions`) |
| `GOOGLE_CLIENT_SECRET` | OAuth 교환·토큰 갱신 | Google Cloud 콘솔에서 새 비밀 추가 → 교체 → 옛 비밀 삭제 | 틀리면 토큰 갱신 실패 → 다시 연결 필요 |
| `KAKAO_REST_API_KEY`, `NAVER_CLIENT_ID`·`NAVER_CLIENT_SECRET` | 장소·검색 조회 | 콘솔에서 재발급 → 교체 | 해당 도구가 failed(조회 실패) |
| `VAPID_PRIVATE_KEY`·`VAPID_PUBLIC_KEY` | 웹 푸시 | 쌍으로 교체 | 기존 구독이 새 키와 맞지 않는다. 사용자가 설정에서 알림을 다시 켜야 한다 |
| `LOGFIRE_TOKEN` | 관찰(`TELEMETRY_ENABLED=true`일 때만) | 교체 | 관찰만 멈춤 |
| `DATABASE_URL` | DB | Railway 참조 변수 | — |

키 보관: 두 암호화 키(`TOKEN_ENCRYPTION_KEY`, `PAYLOAD_ENCRYPTION_KEY`)는 DB 백업과 **다른 곳**(예: 개인 비밀번호 관리자)에도
적어 둔다. DB 백업만 있고 키가 없으면 토큰·메일 기록은 복구되지 않는다.

## 5. 모델·외부 API 장애 때 화면과 대응

| 장애 | 화면에 보이는 것 | 서버 동작 | 대응 |
|---|---|---|---|
| 모델 제공자 오류(5xx, 529 과부하, 키 오류) | task 실패, 채팅에 "작업 중 오류가 발생해 끝내지 못했습니다." 푸시 task_failed. 제공자 응답 내용은 보이지 않는다 | 그 호출의 예산 예약은 unknown으로 남아 오늘 남은 한도에서 빠진 채로 둔다(청구됐을 수 있어서) | 제공자 상태 페이지 확인, 잠시 뒤 다시 요청. 키 문제면 4절 |
| 모델이 도구를 잘못 부름 | "모델이 도구를 올바르게 부르지 못해 작업을 끝내지 못했습니다." | failed | 요청을 바꿔 다시 |
| 하루 비용 한도 | "오늘 쓸 수 있는 비용 한도를 다 써서…" budget_exceeded. 감시는 "오늘은 쉬는 중" | 모델을 부르지 않음 | 내일 자동 해제. 필요하면 `DAILY_COST_LIMIT_USD` |
| 조회 API 실패(날씨·검색·장소·일정 조회·메일) | 작업 기록의 그 단계가 실패, 답변에서 모델이 알림 | failed + retryable. 자동 재시도 없음 | 잠시 뒤 다시. 키·한도(429)는 콘솔 확인 |
| 쓰기 API 시간 초과(일정 만들기·고치기·지우기) | 승인 카드 "확인 필요", task needs_review, 푸시 task_needs_review | operation unknown → 곧바로 재조회. 확인 못 하면 unknown 유지, 다시 실행하지 않음 | Google Calendar에서 직접 확인. 같은 요청을 새로 하면 새 승인이 필요하다 |
| Google 토큰 만료·권한 철회 | 연결 화면 "다시 연결 필요" | 대기 중 계획 무효화 | 다시 연결 |
| DB 중단·서버 재시작 | 요청 실패(5xx). 재시작 뒤 진행 중 작업이 이어짐 | DBOS 복구, outbox 재처리 | Railway 로그 확인. 반복되면 2절 복원 |
| outbox 시작 10번 실패 | 작업이 queued(대기 중) 또는 승인 대기에 머문다 | outbox failed, 로그 "outbox 시작 실패" | 로그로 원인을 고친 뒤 서버를 재시작한다. 시작 때 queued인 채팅 task와 pending 승인이 없는 승인 대기 task를 다시 시작한다(`workflows.restart_orphaned_tasks`, 같은 워크플로 ID라 한 번만 돈다) |

모델 제공자 오류 화면 문구는 `tests/test_outage.py`(가짜 모델이 529를 던짐)로 확인했다. 실제 장애에서 본 것은 아니다.

## 6. 7일 실사용 기록 양식

BUILD_SPEC 9단계 "최소 7일 실사용 기록". 배포한 날부터 하루 한 줄. 비용은 비용 화면(`/usage`, `GET /api/usage/daily`)의
그날 합계, 요청 수·실패는 작업 탭 또는 아래 질의(서울 날짜)로 센다. 대화 내용·개인 정보는 적지 않는다.

```sql
SELECT (created_at AT TIME ZONE 'Asia/Seoul')::date AS day, status, count(*)
FROM tasks GROUP BY 1, 2 ORDER BY 1, 2;
```

| 날짜 | 요청 수(task) | 실패(failed·budget_exceeded) | 확인 필요(needs_review) | 승인 요청/승인/거절 | 비용(USD) | 자동화 실행(예약·감시) | 이상 사항(무엇·어떻게 처리) |
|---|---|---|---|---|---|---|---|
| 1일차 (YYYY-MM-DD) | | | | | | | |
| 2일차 | | | | | | | |
| 3일차 | | | | | | | |
| 4일차 | | | | | | | |
| 5일차 | | | | | | | |
| 6일차 | | | | | | | |
| 7일차 | | | | | | | |

7일 뒤 정리: 가장 잦은 실패 원인, 하루 최대 비용, 승인 우회·잘못된 완료 보고가 있었는지(있으면 바로 기록), 다음에 고칠 것.

## 7. 복원 연습 기록(2026-11-18, 로컬)

**로컬 연습이다. 배포 환경(Railway)의 백업·복원을 검증한 것이 아니다.** 같은 절차를 배포 뒤 Railway에서 다시 해야 한다.

환경: 로컬 docker compose의 `postgres:16` 컨테이너(pg_dump 16.16). 개발 DB `myagent`(alembic head `11dcb5772dd1`,
연습 직전에 `pnpm db:migrate`로 9d3727e1c857 → 11dcb5772dd1 적용). API 서버는 띄우지 않은 상태(행 수가 바뀌지 않게).
테스트 DB(`myagent_test`)와 개발 DB는 지우거나 바꾸지 않았다.

명령(컨테이너 안에서 실행, 덤프는 컨테이너 /tmp):

```powershell
docker exec <컨테이너> pg_dump -U myagent -d myagent -Fc -f /tmp/restore_drill.dump
docker exec <컨테이너> createdb -U myagent myagent_restore_drill
docker exec <컨테이너> pg_restore -U myagent -d myagent_restore_drill --no-owner --exit-on-error /tmp/restore_drill.dump
# 두 DB에서 public·dbos 스키마의 모든 표 count(*)를 같은 질의로 뽑아 Compare-Object
# 인덱스·제약·시퀀스 개수, 시퀀스 last_value 비교
# apps/api에서 $env:DATABASE_URL을 버리는 DB로 두고: uv run python -m alembic current / alembic check
docker exec <컨테이너> dropdb -U myagent myagent_restore_drill
docker exec <컨테이너> rm -f /tmp/restore_drill.dump
```

결과:

| 확인 | 결과 |
|---|---|
| pg_dump / createdb / pg_restore 종료 코드 | 0 / 0 / 0 (전체 약 1초, 덤프 142,298바이트) |
| 표 개수 | 41개(public 27, dbos 14) 모두 복원 |
| 표별 행 수 비교 | 41개 표 모두 같음(차이 0). 개발 DB가 거의 비어 있다: users 1, sessions 3, conversations 1, alembic_version 1, dbos.workflow_status 2·operation_outputs 10·workflow_input 2·workflow_output 2·workflow_schedules 1 등, 나머지 0 |
| 인덱스·제약·시퀀스 개수 | 128·131·26으로 같음, 시퀀스 last_value 26개 모두 같음 |
| `alembic current`(복원 DB) | `11dcb5772dd1 (head)` |
| `alembic check`(복원 DB) | No new upgrade operations detected |
| 정리 | 버리는 DB 삭제, 컨테이너 /tmp 덤프 삭제, 남은 DB는 myagent·myagent_test·postgres(원래대로) |

한계: 개발 DB에 데이터가 거의 없어 큰 데이터의 복원 시간은 알 수 없다. 복원한 DB로 서버를 띄워 DBOS 복구까지 확인하지는 않았다
(서버를 버리는 DB에 붙이면 1분 검사 예약이 등록된다). 암호화 키 없이 복원했을 때의 동작은 4절 설명이며 이번에 시험하지 않았다.
