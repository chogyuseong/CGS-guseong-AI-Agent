# 강제 종료 복구 확인 (3단계)

"작업 도중 서버를 죽였다 다시 켜도 이어서 끝난다"를 직접 확인하는 절차다.
원리와 규칙은 `docs/ARCHITECTURE.md`의 "내구성 실행", 외부 행동 규칙은 `docs/SAFETY.md`의 "at-most-once"를 본다.

## 작업자가 이미 확인한 것 (2026-11-17, 실제 모델 없이)

실제 앱(`create_app`)에 "두 번째 응답에서 8초 기다리는 가짜 모델"만 끼워 127.0.0.1:8010에 띄우고, 버리는 DB(`myagent_probe_test`)로
메시지를 보낸 뒤 `taskkill /T /F`로 프로세스를 강제 종료했다가 다시 켰다(실험 스크립트는 저장소에 넣지 않았다).

| 경우 | 죽인 시점 | 죽인 직후 DB | 다시 켠 뒤 |
| --- | --- | --- | --- |
| 1 | 모델이 add_todo를 부르고(할 일 1개 들어감) 두 번째 응답을 기다리는 중 | task running, step add_todo done, todos 1, dbos 워크플로 PENDING | 로그 `Recovering 1 workflows from application version myagent-3` → 첫 모델 응답은 다시 요청하지 않고, add_todo도 다시 실행하지 않고, 두 번째 응답만 받아 task done, 답변 메시지 1개, **todos 1** |
| 2 | add_todo가 DB에 커밋한 직후(도구 실행 도중) | task running, step add_todo running | 다시 실행하지 않고 step failed "실행 여부를 확인할 수 없습니다…", task done, **할 일이 두 번 들어가지 않음** |

메시지 전송 API는 두 경우 모두 16ms 만에 202를 돌려줬다(실행을 기다리지 않음).

## 사용자가 직접 확인하는 방법 (실제 모델 사용)

비용: 아래 요청은 모델 요청 7~8번이다(모두 FAST 모델. 2026-11-17부터 SMART 자동 전환 없음). 대략 $0.01~0.05이고,
서버가 죽는 동안 진행 중이던 요청 하나는 다시 요청되므로 조금 더 든다. 하루 상한(`DAILY_COST_LIMIT_USD`) 안에서만 돈다.

### 1. 서버를 따로 띄운다

`pnpm dev`는 api와 web을 함께 띄우고 api에 `--reload`가 붙어 있어 강제 종료하기 번거롭다. 터미널 두 개를 연다.

```powershell
# 터미널 1 (저장소 루트): DB와 화면
pnpm db:up
pnpm db:migrate
pnpm web
```

```powershell
# 터미널 2 (apps/api): API를 --reload 없이
cd apps/api
uv run python -m uvicorn app.main:create_app --factory --loop asyncio:SelectorEventLoop --host 127.0.0.1 --port 8000
```

시작 로그에 `DBOS launched!`가 보이면 된다. 처음 켤 때 DB에 `dbos` 스키마가 생긴다.

### 2. 오래 걸리는 요청을 보낸다

브라우저(http://localhost:3000)에서 로그인하고 채팅에 보낸다.

> 할 일에 우유, 계란, 빵, 사과, 바나나를 하나씩 따로 추가하고 마지막에 할 일 목록을 보여줘

### 3. 도중에 강제 종료한다

화면 타임라인에 "할 일에 '…' 추가하는 중"이 두세 개 생겼을 때, 터미널 3에서:

```powershell
$api = (Get-NetTCPConnection -State Listen -LocalPort 8000).OwningProcess
Get-Process -Id $api        # python인지 확인한다(다른 프로젝트 컨테이너가 아닌지)
taskkill /PID $api /T /F
```

(터미널 2를 그냥 닫아도 된다. Ctrl+C는 정상 종료라 "강제 종료" 확인은 아니지만, 그래도 다음 시작 때 이어진다.)

죽은 상태를 DB에서 본다(선택):

```powershell
docker exec my-agent-postgres-1 psql -U myagent -d myagent -c "select id, status, workflow_id from tasks order by id desc limit 1"
docker exec my-agent-postgres-1 psql -U myagent -d myagent -c "select workflow_uuid, status, recovery_attempts from dbos.workflow_status order by created_at desc limit 3"
```

task는 `running`, 워크플로는 `PENDING`이어야 한다. 화면은 계속 "진행 중"으로 보인다.

### 4. 다시 켠다

터미널 2에서 같은 명령으로 API를 다시 켠다. 로그에 `Recovering 1 workflows from application version myagent-3`가 보이고,
화면을 새로 고치지 않아도(폴링) 나머지 단계가 이어서 생기다가 답변이 나온다. 화면을 닫았다 열어도 같은 작업이 보인다.

### 5. 확인할 것

- 작업 기록 탭에서 그 task가 `done`이고 답변이 하나다.
- 할 일 목록에 다섯 개가 **한 번씩만** 있다(죽기 전에 추가된 것이 다시 들어가지 않았다).
- 드물게 도구가 DB에 쓰는 바로 그 순간에 죽였다면 그 step은 "실행 여부를 확인할 수 없습니다"로 실패 표시된다.
  그 할 일은 들어갔을 수도, 아닐 수도 있다(다시 넣지 않는 것이 규칙이다). 목록에서 확인한다.

## 이어지지 않을 때

| 증상 | 원인과 조치 |
| --- | --- |
| 시작 로그가 `No workflows to recover` | 다른 프로세스가 이미 이어서 끝냈거나, `app/agent/workflows.py`의 `WORKFLOW_VERSION`이 바뀌었다(버전이 다른 워크플로는 복구하지 않는다) |
| task가 계속 `queued` | 워크플로를 시작하기 전에 죽었다. 다음 시작 때 `restart_orphaned_tasks`가 시작한다. 그래도 남으면 같은 task id의 워크플로가 dbos 스키마에 이미 끝난 상태로 있는지 본다(DB를 초기화하면서 dbos 스키마를 남긴 경우, 아래) |
| 워크플로가 `MAX_RECOVERY_ATTEMPTS_EXCEEDED` | 복구할 때마다 같은 곳에서 죽는다(기본 100번). API 로그의 오류를 본다 |
| 8000 포트가 이미 쓰는 중 | `Get-NetTCPConnection -LocalPort 8000`의 프로세스를 확인한다. 다른 프로젝트 컨테이너(`cho-web-pj-web-1`)면 건드리지 않는다 |

## DBOS 스키마 정리

DBOS 시스템 테이블은 `myagent` DB의 `dbos` 스키마에 있다(워크플로 상태와 단계 결과, 요청 문장과 모델 응답이 들어 있다).
앱 테이블을 초기화할 때는 함께 지운다. API 서버를 끈 뒤:

```powershell
docker exec my-agent-postgres-1 psql -U myagent -d myagent -c "DROP SCHEMA dbos CASCADE"
```

다음에 API를 켜면 DBOS가 다시 만든다. 테스트 DB(`myagent_test`)는 테스트를 돌릴 때마다 통째로 새로 만들어진다.
