"""3단계: 백그라운드 워커. 테스트 9번(정확히 한 번 실행, 멈춘 작업 회수)이 핵심이다."""

from datetime import timedelta

import pytest
from django.utils import timezone

from agent.llm import FakeLLM, LLMResponse
from agent.models import Task

pytestmark = pytest.mark.django_db


def answer(text="완료했습니다."):
    return LLMResponse(text=text, stop_reason="end_turn")


@pytest.fixture
def fake_llm(monkeypatch):
    llm = FakeLLM([answer(f"답 {i}") for i in range(10)])
    monkeypatch.setattr("agent.loop.get_llm_client", lambda: llm)
    return llm


def make_task(user, conversation, text="안녕"):
    from chat.models import Message

    message = Message.objects.create(conversation=conversation, role=Message.Role.USER, content=text)
    task = Task.objects.create(user=user, conversation=conversation, trigger_message=message)
    message.task = task
    message.save()
    return task


# ── 테스트 9: 정확히 한 번 ───────────────────────────────


def test_9_worker_runs_queued_task_exactly_once(user, conversation, fake_llm):
    from agent import worker

    task = make_task(user, conversation)

    assert worker.run_once() == task.id
    assert worker.run_once() is None  # 두 번째 반복에서는 잡을 것이 없다
    task.refresh_from_db()
    assert task.status == Task.Status.DONE
    assert len(fake_llm.calls) == 1


def test_9_running_the_same_task_twice_executes_once(user, conversation, fake_llm):
    from agent.loop import run_task

    task = make_task(user, conversation)
    run_task(task.id)
    run_task(task.id)  # 두 워커가 같은 Task를 잡으려 한 상황

    assert len(fake_llm.calls) == 1


def test_9_oldest_queued_task_goes_first(user, conversation, fake_llm):
    from agent import worker

    first = make_task(user, conversation, "첫 번째")
    second = make_task(user, conversation, "두 번째")

    assert worker.run_once() == first.id
    assert worker.run_once() == second.id


def test_9_stuck_running_task_is_failed_after_five_minutes(user, conversation):
    from agent import worker
    from chat.models import Message

    stuck = make_task(user, conversation)
    Task.objects.filter(id=stuck.id).update(
        status=Task.Status.RUNNING, locked_at=timezone.now() - timedelta(minutes=6)
    )
    fresh = make_task(user, conversation, "방금 시작")
    Task.objects.filter(id=fresh.id).update(status=Task.Status.RUNNING, locked_at=timezone.now())

    assert worker.reclaim_stuck_tasks() == 1
    stuck.refresh_from_db()
    fresh.refresh_from_db()
    assert stuck.status == Task.Status.FAILED
    assert stuck.error
    assert fresh.status == Task.Status.RUNNING
    assert Message.objects.filter(task=stuck, role=Message.Role.ASSISTANT).exists()


def test_9_external_step_stuck_after_approval_is_failed(user, conversation):
    """승인 후 외부 호출 도중 서버가 죽으면 Step이 running으로 남는다. 5분 뒤 정리한다."""
    from agent import worker
    from agent.models import RiskLevel, Step

    task = make_task(user, conversation)
    Task.objects.filter(id=task.id).update(status=Task.Status.WAITING_APPROVAL)
    step = Step.objects.create(
        task=task, order=1, tool_name="x", risk_level=RiskLevel.EXTERNAL, status=Step.Status.RUNNING
    )
    Step.objects.filter(id=step.id).update(updated_at=timezone.now() - timedelta(minutes=6))

    assert worker.reclaim_stuck_tasks() == 1
    task.refresh_from_db()
    step.refresh_from_db()
    assert (task.status, step.status) == (Task.Status.FAILED, Step.Status.FAILED)


def test_worker_survives_a_crashing_task(user, conversation, monkeypatch):
    from agent import worker

    task = make_task(user, conversation)

    def boom(task_id, **kwargs):
        raise RuntimeError("버그")

    monkeypatch.setattr(worker, "run_task", boom)
    assert worker.run_once() == task.id  # 예외가 워커 밖으로 새지 않는다
    task.refresh_from_db()
    assert task.status == Task.Status.FAILED


def test_maintenance_expires_old_approvals(user, conversation, monkeypatch):
    from agent import worker

    called = []
    monkeypatch.setattr(worker.approvals, "expire_old", lambda: called.append(1) or 0)
    worker.run_maintenance()
    assert called == [1]


def test_heartbeat_keeps_long_task_from_being_reclaimed(user, conversation):
    """오래 걸려도 LLM을 부를 때마다 locked_at이 갱신되므로 '멈춘 작업'으로 오인되지 않는다."""
    from agent.loop import run_task

    task = make_task(user, conversation)
    seen = []

    class SlowLLM(FakeLLM):
        def complete(self, **kwargs):
            seen.append(Task.objects.get(id=task.id).locked_at)
            return super().complete(**kwargs)

    run_task(task.id, llm=SlowLLM([answer()]))
    assert seen[0] is not None


# ── 취소 ─────────────────────────────────────────────────


def test_cancel_queued_task(logged_in_client, user, conversation):
    task = make_task(user, conversation)

    response = logged_in_client.post(f"/tasks/{task.id}/cancel/")

    assert response.status_code == 302
    task.refresh_from_db()
    assert task.status == Task.Status.CANCELLED


def test_cancelled_while_running_stops_and_keeps_cancelled(user, conversation):
    from agent.loop import run_task
    from chat.models import Message

    task = make_task(user, conversation)

    class CancelDuringCall(FakeLLM):
        def complete(self, **kwargs):
            Task.objects.filter(id=task.id).update(status=Task.Status.CANCELLED)
            return super().complete(**kwargs)

    run_task(task.id, llm=CancelDuringCall([answer("취소 뒤 답")]))
    task.refresh_from_db()

    assert task.status == Task.Status.CANCELLED  # 끝난 뒤에도 done으로 덮어쓰지 않는다
    assert not Message.objects.filter(task=task, content="취소 뒤 답").exists()


def test_cancel_pending_approval_rejects_it(logged_in_client, user, conversation):
    from agent.models import Approval, RiskLevel, Step

    task = make_task(user, conversation)
    Task.objects.filter(id=task.id).update(status=Task.Status.WAITING_APPROVAL)
    step = Step.objects.create(
        task=task, order=1, tool_name="x", risk_level=RiskLevel.EXTERNAL, status=Step.Status.WAITING_APPROVAL
    )
    approval = Approval.objects.create(step=step, summary="외부 행동")

    logged_in_client.post(f"/tasks/{task.id}/cancel/")

    approval.refresh_from_db()
    assert approval.status == Approval.Status.REJECTED


def test_cannot_cancel_finished_or_others_task(client, other_user, user, conversation):
    task = make_task(user, conversation)
    Task.objects.filter(id=task.id).update(status=Task.Status.DONE)

    client.force_login(other_user)
    assert client.post(f"/tasks/{task.id}/cancel/").status_code == 404
    client.force_login(user)
    client.post(f"/tasks/{task.id}/cancel/")
    task.refresh_from_db()
    assert task.status == Task.Status.DONE


# ── 진행 표시(HTMX 폴링) ─────────────────────────────────


def test_steps_fragment_polls_while_active(logged_in_client, user, conversation):
    task = make_task(user, conversation)

    response = logged_in_client.get(f"/tasks/{task.id}/steps/")

    body = response.content.decode()
    assert response.status_code == 200
    assert 'hx-trigger="every 1s"' in body
    assert "HX-Refresh" not in response


def test_steps_fragment_asks_page_refresh_when_finished(logged_in_client, user, conversation):
    task = make_task(user, conversation)
    Task.objects.filter(id=task.id).update(status=Task.Status.DONE)

    response = logged_in_client.get(f"/tasks/{task.id}/steps/")

    assert response["HX-Refresh"] == "true"


def test_steps_fragment_is_private(client, other_user, user, conversation):
    task = make_task(user, conversation)
    client.force_login(other_user)
    assert client.get(f"/tasks/{task.id}/steps/").status_code == 404


def test_chat_page_shows_timeline_for_active_task(logged_in_client, user, conversation):
    task = make_task(user, conversation)

    body = logged_in_client.get(f"/c/{conversation.id}/").content.decode()

    assert f"/tasks/{task.id}/steps/" in body
    assert f"/tasks/{task.id}/cancel/" in body


def test_task_history_page_lists_my_tasks(logged_in_client, user, other_user, conversation):
    from chat.models import Conversation

    make_task(user, conversation, "내 요청")
    other_conversation = Conversation.objects.create(user=other_user)
    make_task(other_user, other_conversation, "남의 요청")

    body = logged_in_client.get("/tasks/").content.decode()

    assert "내 요청" in body
    assert "남의 요청" not in body
