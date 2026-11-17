# 상태 전이 표 (BUILD_SPEC 4.2·9.2, 9단계 보강 2026-11-18)

task, operation(외부 작업 하나), approval, outbox 요청, 자동화 발생 기록의 상태와 전이를 **지금 코드에서 읽어** 정리했다.
"누가"는 상태를 바꾸는 함수다(`모듈.함수`, 모듈은 아래 약칭). 표에 없는 전이는 코드가 하지 않으며 하면 안 된다.

| 약칭 | 파일 |
|---|---|
| `runner` | apps/api/app/agent/runner.py |
| `approvals` | apps/api/app/agent/approvals.py |
| `cancel` | apps/api/app/agent/cancel.py |
| `executor` | apps/api/app/agent/executor.py |
| `outbox` | apps/api/app/agent/outbox.py |
| `tracking` | apps/api/app/agent/tracking.py |
| `google_auth` | apps/api/app/google_auth.py |
| `check` | apps/api/app/automation/check.py |
| `conversations` | apps/api/app/routers/conversations.py |

`tests/test_state_machines.py`가 이 문서와 코드를 맞춰 본다: 각 절의 "상태 목록"이 enum 값과 같은지, 표에 쓴 상태가
enum에 있는지, "누가" 칸의 함수가 실제로 있는지. 상태·전이를 바꾸면 이 문서도 같이 고친다.

공통 규칙(BUILD_SPEC 4.2):

- operation `succeeded`는 영수증(execution_receipts)이 있어야 한다. 영수증을 만드는 곳(`executor.finish_execution`,
  `executor.reconcile_operation`)만 succeeded로 바꾼다. 영수증 없는 성공 결과는 `tracking.receipt_checked`가 unknown으로 바꾼다.
- `unknown`은 `failed`가 아니다(실행했는지 모름). unknown인 operation은 새 operation_id로 같은 작업을 자동으로 만들지 않고,
  같은 operation을 다시 실행하지도 않는다(`executor.early_result`가 already_executing을 돌려준다).
- 재조회로 확인하지 못하면 task는 `needs_review`(확인 필요)로 끝나고 푸시 task_needs_review를 보낸다.
- 상태를 바꾸는 곳은 해당 행(task, operation)을 `FOR UPDATE`로 잠그거나 조건부 UPDATE(`WHERE status IN (...)`)로 바꾼다.

---

## 1. task (`TaskStatus`)

상태 목록(TaskStatus): queued, running, waiting_input, waiting_approval, cancel_requested, reconciling, done, partially_done, failed, cancelled, budget_exceeded, needs_review

| 전이 | 누가 | 조건·비고 |
|---|---|---|
| (새 행) → `queued` | `conversations.save_message_and_task`, `check.create_schedule_task` | 메시지 저장과 같은 트랜잭션에 outbox run 요청. 예약은 워크플로 안에서 만들고 바로 실행 |
| `queued` → `running` | `runner.claim_run` (`runner.mark_claimed`) | task 행 잠금. queued일 때만. 같은 워크플로의 복구 claim은 running 그대로 다시 잡음(`runner.reclaimed`) |
| `waiting_approval` → `running` | `runner.claim_resume` | pending 승인이 0개일 때만(마지막 결정 뒤 outbox resume 요청) |
| `running` → `done` | `runner.finish` (`runner.save_outcome`) | 모델이 최종 답을 냄. 결과 불명 operation이 없을 때만 |
| `running` → `needs_review` | `runner.save_outcome` | done이어야 했지만 이 task에 unknown·reconciling·executing operation이 남음(`runner.has_unresolved_operation`) |
| `running` → `waiting_approval` | `runner.finish` | EXTERNAL 호출이 승인 대기(DeferredToolRequests). 채팅 task만. 승인 요청·계획·operation을 같은 트랜잭션에 저장 |
| `running` → `failed` | `runner.execute_agent`, `runner.finish`, `runner.prepare_resume` | 모델 응답 이상, 가격표에 없는 모델, 예상 못 한 오류, 계획을 만들 수 없음(PlanRejectedError), 자동 작업의 승인 요청 |
| `running` → `budget_exceeded` | `runner.prepare_run`, `runner.prepare_resume`, `runner.execute_agent` | 하루 비용 상한(시작 전·모델 호출 예약 때), 작업당 요청 수·토큰 상한 |
| `running` → `cancel_requested` | `cancel.request_cancel` | 실행 중 취소 요청. 실행 쪽이 다음 모델 호출·도구 실행 직전에 멈춘다 |
| `cancel_requested` → `cancelled` | `runner.finish` | TaskCancelledError로 멈춤. 승인 대기 결과가 와도 승인을 만들지 않고 cancelled |
| `cancel_requested` → `done`, `needs_review`, `failed`, `budget_exceeded` | `runner.finish` | 취소 확인 전에 run이 끝났다. 이미 한 일을 숨기지 않는다 |
| `queued`, `waiting_approval` → `cancelled` | `cancel.close_for_cancel` (`cancel.request_cancel`) | 결정 전 승인 cancelled, 실행 전 operation cancelled, 승인 대기 step failed |
| `waiting_approval` → `cancelled` | `approvals.expire_task` (`check.expire_overdue_approvals`, `approvals.decide`) | 승인 30분 만료. 1분 검사가 정리하고, 만료 뒤 결정 요청도 여기서 정리 |

끝난 상태: done, needs_review, failed, cancelled, budget_exceeded. 끝난 task는 다시 시작하지 않는다
(claim은 queued·waiting_approval만, 취소는 `CancelNotAllowedError` 409, finish는 running·cancel_requested일 때만 저장).
`IN_PROGRESS_STATUSES`(apps/api/app/automation/retention.py)는 보관 정리와 대화 삭제(409)에서 "끝나지 않은 작업"으로 본다.

정의만 있고 지금 코드가 쓰지 않는 상태(코드와 명세의 차이, docs/STATUS.md 9단계):

- `waiting_input`: 되묻기는 일반 답변으로 끝난다(`done`). BUILD_SPEC 9.2 "모호한 요청은 waiting_input 또는 일반 질문 응답"의 뒤쪽을 택한 상태다.
- `reconciling`: 재조회는 도구 실행 안에서(operation `reconciling`) 끝나서 task는 그동안 `running`이다.
- `partially_done`: 일부 외부 작업 뒤 예산 초과·취소면 `budget_exceeded`·`cancelled`로 끝나고 영수증(승인 카드)과
  "취소 전에 끝난 외부 작업 N건" 문장으로 완료한 일을 보여 준다(`cancel.completed_external_note`).

## 2. operation (`OperationStatus`)

외부 작업 하나(EXTERNAL 도구 호출 하나, `action_plans` 1:1). BUILD_SPEC 4.2의 12개 상태.

상태 목록(OperationStatus): planned, awaiting_approval, approved, executing, succeeded, failed, unknown, reconciling, rejected, expired, stale, cancelled

| 전이 | 누가 | 조건·비고 |
|---|---|---|
| (새 행) → `awaiting_approval` | `approvals.save_plan` (`approvals.save_requests`) | 계획·승인·감시 결과와 같은 트랜잭션 |
| `awaiting_approval` → `approved` | `approvals.apply_decision` (`approvals.decide`) | 사용자가 승인. 실행 유효 시간 = 승인 + 5분(`execute_before`) |
| `awaiting_approval` → `rejected` | `approvals.apply_decision` | 사용자가 거절 |
| `awaiting_approval` → `expired` | `approvals.expire_task` | 승인 30분 만료 |
| `awaiting_approval`, `approved` → `cancelled` | `cancel.close_for_cancel`, `runner.close_unexecuted_steps`, `google_auth.invalidate_pending_plans` | 작업 취소 / 실행 전에 task가 끝남(9단계 보강) / 연결 해제·만료(error_code connection_removed·connection_revoked) |
| `approved` → `executing` | `executor.begin_execution` | 소유권·승인·인자 해시·task running·유효 시간·도구 버전·연결 검사를 모두 통과. 시도 횟수 +1 |
| `approved` → `cancelled` | `executor.begin_execution` (`executor.refusal_for`) | task가 running이 아님(취소 요청) |
| `approved` → `expired` | `executor.refusal_for` | 실행 유효 시간(승인 + 5분)이 지남 |
| `approved` → `stale` | `executor.refusal_for` | 도구 규격 버전이 계획과 다름 |
| `approved` → `failed` | `executor.refusal_for` | 승인 뒤 외부 계정 연결이 바뀌거나 해제됨(connection_changed) |
| `executing` → `succeeded` | `executor.finish_execution` | **영수증을 같은 트랜잭션에 만든다** |
| `executing` → `failed` | `executor.finish_execution` | 외부 API가 실패를 확정 |
| `executing` → `stale` | `executor.finish_execution` (`executor.operation_status_for`) | 대상이 승인 뒤 바뀜(target_changed, 일정 ETag 412 등). 다시 승인 필요 |
| `executing` → `unknown` | `executor.finish_execution` | 쓰기 요청 시간 초과·연결 끊김. 곧바로 재조회한다 |
| `executing`, `unknown` → `reconciling` | `executor.start_reconcile` (`executor.reconcile_operation`, `tracking.close_uncertain_step`) | 결과 불명 직후, 또는 복구 때 실행 도중 끊긴 호출 |
| `reconciling` → `succeeded` | `executor.reconcile_operation` | 외부에 처리 기록이 있음. **영수증을 만든다** |
| `reconciling` → `failed` | `executor.reconcile_operation` | 처리 기록이 없다고 확인(not_executed) |
| `reconciling` → `unknown` | `executor.reconcile_operation` | 확인 못 함. task는 needs_review |

금지된 전이(코드가 막는 것):

- 영수증 없이 `succeeded`. 실행기는 succeeded인데 영수증이 없으면 완료로 보지 않고 unknown(no_receipt)을 돌려준다(`executor.receipt_result`).
- `awaiting_approval`·`planned`에서 바로 `executing`: `executor.early_result`가 PermissionError(권한 위반).
- `executing`·`unknown`·`reconciling`에서 다시 `executing`: already_executing(unknown)을 돌려주고 외부를 부르지 않는다.
- 끝난 operation(succeeded, failed, rejected, expired, stale, cancelled)에서 다른 상태로: 다시 실행하지 않는다
  (succeeded는 저장된 영수증 재사용, 나머지는 operation_closed 등 실패 결과).
- `unknown`에서 새 operation 만들기: 하지 않는다. 같은 요청이 필요하면 사용자가 새로 요청해 새 승인을 받는다.

`planned`는 지금 DB에 쓰지 않는다. 실행 계획 초안(`plans.draft_plan`)은 메모리에서만 만들고, 저장할 때 곧바로
`awaiting_approval`로 넣는다(승인 없이 저장되는 계획이 없다). 실행기는 planned도 미승인으로 다룬다.
`unknown`인 operation을 나중에 자동으로 다시 조회하는 주기 작업은 없다(후속 과제). 화면은 확인 필요로 보여 준다.

## 3. approval (`ApprovalStatus`)

상태 목록(ApprovalStatus): pending, approved, rejected, expired, cancelled

| 전이 | 누가 | 조건·비고 |
|---|---|---|
| (새 행) → `pending` | `approvals.save_plan` | 만료 = 생성 + 30분(`decision_expires_at`) |
| `pending` → `approved` | `approvals.apply_decision` (`approvals.decide`) | task·승인 행 잠금. task가 waiting_approval이고 만료 전. 감시 결과 mismatch면 확인(confirm_mismatch) 필요 |
| `pending` → `rejected` | `approvals.apply_decision` | step도 rejected |
| `pending` → `expired` | `approvals.expire_task` (`check.expire_overdue_approvals`, `approvals.decide`) | step expired, task cancelled |
| `pending` → `cancelled` | `cancel.close_for_cancel` | 결정 전에 작업 취소 |

금지된 전이: pending이 아닌 승인은 다시 바뀌지 않는다(같은 결정을 다시 보내면 changed=false로 그대로 응답).
승인된 승인은 task가 실행 전에 끝나도 `approved`로 남는다(사용자의 결정 기록). 실행 여부는 operation과 step이 말한다.
마지막 결정이 나면 같은 트랜잭션에 outbox resume 요청을 남긴다.

## 4. outbox 요청 (`OutboxStatus`)

상태 목록(OutboxStatus): pending, dispatched, failed

| 전이 | 누가 | 조건·비고 |
|---|---|---|
| (새 행) → `pending` | `outbox.add_run_event`, `outbox.add_resume_event` | 같은 dedupe_key면 새로 만들지 않음(`outbox.add_once`) |
| `pending` → `dispatched` | `outbox.record` (`outbox.dispatch_events`) | 고정 워크플로 ID로 시작 성공. 다른 dispatcher가 먼저 처리했으면 그대로 |
| `pending` → `pending` | `outbox.record` | 시작 실패. attempts +1, next_attempt_at 뒤로(30초부터 두 배, 최대 10분), last_error는 오류 종류만 |
| `pending` → `failed` | `outbox.record` | 10번 실패. 더 시도하지 않는다 |

dispatched·failed는 바뀌지 않는다(조건부 UPDATE `WHERE status = pending`). failed가 되면 그 task는 queued 또는
waiting_approval(pending 승인 0개)에 머물고 로그로만 알 수 있다. 서버를 다시 시작하면 시작 검사
(apps/api/app/agent/workflows.py `restart_orphaned_tasks`)가 그런 task를 같은 워크플로 ID로 다시 시작한다(docs/OPERATIONS.md 5절).

## 5. 자동화 발생 기록 (automation_occurrences)

enum이 아니라 문자열(`check.OCCURRENCE_STARTED`, `check.OCCURRENCE_SKIPPED`)이고 **전이가 없다**(한 번 쓰고 바꾸지 않는다).
(kind, source_id, scheduled_for) 유일 조건으로 같은 예정 시각을 두 번 실행하지 않는다.

| 기록 | 누가 | 조건·비고 |
|---|---|---|
| (새 행) → started | `check.claim_schedule_run`, `check.claim_watch_run` | 예약이면 task_id(그 task는 queued로 시작) |
| (새 행) → skipped | `check.claim_schedule_run`, `check.claim_watch_run` | skip_reason: missed(유예 2시간 넘김), expired, budget |

## 6. 참고: step (`StepStatus`)

화면의 작업 기록 한 줄.

상태 목록(StepStatus): running, waiting_approval, done, failed, rejected, expired, unknown

| 전이 | 누가 | 조건·비고 |
|---|---|---|
| (새 행) → `running` | `tracking.start_step` | READ·INTERNAL 호출, 승인 없는 경로 |
| (새 행) → `waiting_approval` | `approvals.save_requests` | EXTERNAL 호출 |
| `waiting_approval` → `running` | `executor.begin_execution` | operation이 executing이 될 때 |
| `waiting_approval` → `rejected` | `approvals.apply_decision` | 거절 |
| `waiting_approval` → `expired` | `approvals.expire_task` | 만료 |
| `waiting_approval` → `failed` | `cancel.close_for_cancel`, `runner.close_unexecuted_steps` | 실행하지 않았다는 것이 확실함(output에 그 문장) |
| `running` → `done`, `failed`, `unknown` | `tracking.finish_step` | 도구 결과(ToolResult.status). 예외는 failed |
| `running` → `done`, `failed`, `unknown` | `tracking.close_uncertain_step` | 복구 때 도중에 끊긴 INTERNAL(unknown)·EXTERNAL(재조회 결과) |

`waiting_approval`에서 실행기가 승인 전에 거절한 경우(operation expired·stale·failed·cancelled)는 step이 `running`을 거치지
않고 도구 결과로 `failed`가 된다(`tracking.finish_step`).
