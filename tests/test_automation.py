"""6단계: 예약 작업, 조건 감시, 아침 브리핑. 스스로 도는 작업은 조회(READ)만 한다(절대 원칙 4)."""

import json
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from django.utils import timezone

from agent.llm import FakeLLM, LLMResponse, ToolCall
from agent.models import Approval, RiskLevel, Step, Task
from agent.registry import Tool, ok_result

pytestmark = pytest.mark.django_db
SEOUL = ZoneInfo("Asia/Seoul")


def at(year, month, day, hour, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=SEOUL)


def run_tool(name, user, **kwargs):
    from agent.registry import get_tool
    from agent.registry import run_tool as run

    return run(get_tool(name), user, kwargs)


# ── 다음 실행 시각 ───────────────────────────────────────


def test_next_run_same_day_if_time_not_passed():
    from automation.services import next_run_after

    # 2026-11-16은 월요일(0). 07:30 매일 → 06:00 기준 같은 날 07:30
    assert next_run_after(at(2026, 11, 16, 6), time(7, 30), []) == at(2026, 11, 16, 7, 30)


def test_next_run_next_day_if_time_passed():
    from automation.services import next_run_after

    assert next_run_after(at(2026, 11, 16, 8), time(7, 30), []) == at(2026, 11, 17, 7, 30)


def test_next_run_respects_weekdays():
    from automation.services import next_run_after

    # 일요일(6)만: 월요일 08시 기준 다음 실행은 그 주 일요일(11/22)
    assert next_run_after(at(2026, 11, 16, 8), time(21, 0), [6]) == at(2026, 11, 22, 21, 0)


# ── 예약 작업 만들기와 제한 ─────────────────────────────


def test_create_schedule_tool(user):
    from automation.models import ScheduledJob

    result = run_tool(
        "create_schedule",
        user,
        title="다음 주 일정 요약",
        prompt_text="다음 주 일정을 요약해라.",
        time_of_day="21:00",
        weekdays=[6],
    )
    assert result["ok"] is True
    job = ScheduledJob.objects.get(user=user, title="다음 주 일정 요약")
    assert job.weekdays == [6] and job.time_of_day == time(21, 0)
    assert job.next_run_at > timezone.now()
    assert "매주 일요일 21:00" in result["data"]["repeat"]  # 등록 후 주기를 말해 줄 수 있게


def test_schedule_limit_10_active(user):
    from automation.models import ScheduledJob

    for i in range(10):
        ScheduledJob.objects.create(
            user=user, title=f"작업 {i}", prompt_text="x", time_of_day=time(9), next_run_at=timezone.now()
        )
    result = run_tool(
        "create_schedule", user, title="하나 더", prompt_text="x", time_of_day="09:00", weekdays=[]
    )
    assert result["ok"] is False and "10" in result["error"]


def test_schedule_time_format_is_checked():
    from agent.registry import get_tool, validate_input

    tool = get_tool("create_schedule")
    assert validate_input(tool, {"title": "a", "prompt_text": "b", "time_of_day": "9시", "weekdays": []})
    assert validate_input(tool, {"title": "a", "prompt_text": "b", "time_of_day": "09:00", "weekdays": [7]})


def test_list_and_delete_schedule(user):
    created = run_tool(
        "create_schedule", user, title="요약", prompt_text="요약해", time_of_day="08:00", weekdays=[]
    )
    titles = [s["title"] for s in run_tool("list_schedules", user)["data"]["schedules"]]
    assert titles == ["아침 브리핑", "요약"]  # 계정마다 기본으로 있는 아침 브리핑 + 새로 만든 것(시각순)

    assert run_tool("delete_schedule", user, id=created["data"]["id"])["ok"] is True
    titles = [s["title"] for s in run_tool("list_schedules", user)["data"]["schedules"]]
    assert titles == ["아침 브리핑"]


# ── 테스트 10: 조건 감시 제한 ────────────────────────────


def watch_input(**overrides):
    base = {
        "description": "내일 서울 비 확률 60% 넘으면 알림",
        "tool_name": "get_weather",
        "tool_input": {"place": "서울", "date": "2026-11-17"},
        "condition_text": "강수 확률이 60%를 넘는다",
        "interval_minutes": 60,
        "repeat": False,
    }
    return {**base, **overrides}


def test_create_watch_with_read_tool(user):
    from automation.models import Watch

    result = run_tool("create_watch", user, **watch_input())
    assert result["ok"] is True
    watch = Watch.objects.get(user=user)
    assert watch.interval_minutes == 60
    assert (watch.expires_at - timezone.now()).days in (29, 30)  # 기본 만료 30일
    assert "만료" in json.dumps(result["data"], ensure_ascii=False)


@pytest.mark.parametrize("tool_name", ["add_todo", "create_event", "remember", "no_such_tool"])
def test_10_watch_rejects_non_read_tools(user, tool_name):
    from automation.models import Watch

    result = run_tool("create_watch", user, **watch_input(tool_name=tool_name, tool_input={}))
    assert result["ok"] is False
    assert not Watch.objects.exists()


@pytest.mark.parametrize("tool_name", ["list_mail", "read_mail"])
def test_10_watch_rejects_mail_tools_even_if_read(user, tool_name, monkeypatch):
    """메일 도구(7단계)는 READ여도 조건 감시에 쓸 수 없다. 메일 내용을 주기적으로 훑지 않는다."""
    from agent import registry
    from automation.models import Watch

    fake_mail = Tool(
        name=tool_name, description="x", input_schema={"type": "object"}, risk_level=RiskLevel.READ,
        run=lambda user, **_: ok_result(), label=lambda **_: "메일", external_content=True,
    )  # fmt: skip
    monkeypatch.setitem(registry._TOOLS, tool_name, fake_mail)
    result = run_tool("create_watch", user, **watch_input(tool_name=tool_name, tool_input={}))

    assert result["ok"] is False
    assert not Watch.objects.exists()


def test_10_watch_rejects_external_content_read_tools_except_web_search(user):
    """외부 글을 가져오는 READ 도구는 web_search만 허용한다(list_events는 거부)."""
    rejected = run_tool(
        "create_watch",
        user,
        **watch_input(
            tool_name="list_events", tool_input={"date_from": "2026-11-17", "date_to": "2026-11-17"}
        ),
    )
    allowed = run_tool(
        "create_watch", user, **watch_input(tool_name="web_search", tool_input={"query": "콘서트 예매 오픈"})
    )

    assert rejected["ok"] is False
    assert allowed["ok"] is True


def test_10_watch_interval_under_30_minutes_rejected(user):
    from automation.models import Watch

    result = run_tool("create_watch", user, **watch_input(interval_minutes=10))
    assert result["ok"] is False and "30" in result["error"]
    assert not Watch.objects.exists()


def test_watch_tool_input_must_match_tool_schema(user):
    result = run_tool("create_watch", user, **watch_input(tool_input={"city": "서울"}))
    assert result["ok"] is False


def test_watch_limit_10_active(user):
    for i in range(10):
        assert run_tool("create_watch", user, **watch_input(description=f"감시 {i}"))["ok"] is True
    result = run_tool("create_watch", user, **watch_input(description="하나 더"))
    assert result["ok"] is False and "10" in result["error"]


# ── 테스트 5, 14: 자동 작업의 Task ────────────────────────


def test_5_due_schedule_creates_read_only_task(user):
    from agent.loop import run_task
    from automation.models import ScheduledJob
    from automation.services import run_due_schedules

    job = ScheduledJob.objects.create(
        user=user, title="요약", prompt_text="할 일을 요약해라.", time_of_day=time(9),
        next_run_at=timezone.now() - timedelta(minutes=1),
    )  # fmt: skip
    assert run_due_schedules() == 1

    task = Task.objects.get(user=user)
    assert task.origin == Task.Origin.SCHEDULE
    assert task.status == Task.Status.QUEUED
    assert task.conversation.title == "예약 작업"
    llm = FakeLLM([LLMResponse(text="요약입니다.")])
    run_task(task.id, llm=llm)
    names = {t["name"] for t in llm.calls[0]["tools"]}
    read_tools = {
        "get_weather", "search_places", "web_search", "list_events",
        "list_todos", "list_memories", "list_schedules", "list_watches",
    }  # fmt: skip
    assert names and names <= read_tools
    job.refresh_from_db()
    assert job.last_run_at is not None
    assert job.next_run_at > timezone.now()  # 다음 실행 시각으로 넘어간다


def test_schedule_not_due_is_not_run(user):
    from automation.models import ScheduledJob
    from automation.services import run_due_schedules

    ScheduledJob.objects.create(
        user=user,
        title="요약",
        prompt_text="x",
        time_of_day=time(9),
        next_run_at=timezone.now() + timedelta(hours=1),
    )
    assert run_due_schedules() == 0
    assert not Task.objects.exists()


def test_inactive_schedule_is_not_run(user):
    from automation.models import ScheduledJob
    from automation.services import run_due_schedules

    ScheduledJob.objects.create(
        user=user, title="요약", prompt_text="x", time_of_day=time(9),
        next_run_at=timezone.now() - timedelta(minutes=1), is_active=False,
    )  # fmt: skip
    assert run_due_schedules() == 0


@pytest.mark.parametrize("origin", [Task.Origin.SCHEDULE, Task.Origin.WATCH])
def test_14_automated_tasks_cannot_create_approval(user, conversation, origin):
    from agent.approvals import request_approval

    task = Task.objects.create(user=user, conversation=conversation, origin=origin)
    step = Step.objects.create(task=task, order=1, tool_name="create_event", risk_level=RiskLevel.EXTERNAL)
    with pytest.raises(PermissionError):
        request_approval(step, guard_llm=FakeLLM([]))
    assert not Approval.objects.exists()


def test_14_scheduled_task_calling_external_tool_is_refused(user):
    """모델이 외부 도구를 불러도 실행하지 않고, 승인 요청도 만들지 않는다."""
    from agent.loop import run_task
    from automation.models import ScheduledJob
    from automation.services import run_due_schedules

    ScheduledJob.objects.create(
        user=user, title="정리", prompt_text="일정을 정리해라.", time_of_day=time(9),
        next_run_at=timezone.now() - timedelta(minutes=1),
    )  # fmt: skip
    run_due_schedules()
    task = Task.objects.get()
    call = LLMResponse(
        text="",
        tool_calls=[
            ToolCall(
                "t1", "create_event", {"title": "x", "start": "2027-01-15T19:00", "end": "2027-01-15T20:00"}
            )
        ],
    )
    run_task(task.id, llm=FakeLLM([call, LLMResponse(text="[제안] 일정을 추가해 드릴까요?")]))

    assert not Approval.objects.exists()
    assert task.steps.get().ok is False


# ── 조건 감시 실행 ──────────────────────────────────────


@pytest.fixture
def weather_tool(monkeypatch):
    """조건 감시가 부를 날씨 도구를 가짜로 바꾼다(네트워크 없음)."""
    from agent import registry

    calls = []
    tool = registry.get_tool("get_weather")

    def fake_run(user, **kwargs):
        calls.append(kwargs)
        return ok_result({"precipitation_probability_max": 80}, sources=["https://open-meteo.com/"])

    import dataclasses

    monkeypatch.setitem(registry._TOOLS, "get_weather", dataclasses.replace(tool, run=fake_run))
    return calls


def make_watch(user, **overrides):
    from automation.models import Watch

    fields = {
        "user": user, "description": "비 오면 알림", "tool_name": "get_weather",
        "tool_input_json": {"place": "서울", "date": "2026-11-17"}, "condition_text": "강수 확률 60% 초과",
        "interval_minutes": 60, "next_run_at": timezone.now() - timedelta(minutes=1),
    }  # fmt: skip
    return Watch.objects.create(**{**fields, **overrides})


def judge(met, summary="내일 비 올 확률 80%"):
    return LLMResponse(text=json.dumps({"met": met, "summary": summary}, ensure_ascii=False))


def test_watch_met_posts_message_notifies_and_stops(user, weather_tool, monkeypatch):
    from agent.models import UsageLog
    from automation.services import run_due_watches
    from chat.models import Message

    pushed = []
    monkeypatch.setattr("automation.services.notify", lambda u, kind, url: pushed.append(kind))
    watch = make_watch(user)

    assert run_due_watches(llm=FakeLLM([judge(True)])) == 1

    watch.refresh_from_db()
    assert weather_tool == [{"place": "서울", "date": "2026-11-17"}]
    message = Message.objects.get(conversation__title="조건 감시")
    assert "내일 비 올 확률 80%" in message.content and "open-meteo.com" in message.content
    assert pushed == ["watch_met"]
    assert watch.is_active is False  # repeat=False면 한 번 알리고 끈다
    assert watch.last_notified_at is not None
    assert UsageLog.objects.filter(purpose="watch").count() == 1  # 판단 비용도 기록한다


def test_watch_repeat_waits_12_hours(user, weather_tool, monkeypatch):
    from automation.services import run_due_watches

    monkeypatch.setattr("automation.services.notify", lambda *a: None)
    watch = make_watch(user, repeat=True)
    run_due_watches(llm=FakeLLM([judge(True)]))

    watch.refresh_from_db()
    assert watch.is_active is True
    assert timedelta(hours=11, minutes=59) < watch.next_run_at - timezone.now() <= timedelta(hours=12)


def test_watch_not_met_checks_again_after_interval(user, weather_tool, monkeypatch):
    from automation.services import run_due_watches
    from chat.models import Message

    pushed = []
    monkeypatch.setattr("automation.services.notify", lambda *a: pushed.append(a))
    watch = make_watch(user)
    run_due_watches(llm=FakeLLM([judge(False, "강수 확률 20%")]))

    watch.refresh_from_db()
    assert pushed == [] and not Message.objects.exists()
    assert watch.is_active is True
    assert timedelta(minutes=59) < watch.next_run_at - timezone.now() <= timedelta(minutes=60)
    assert watch.last_result_json["summary"] == "강수 확률 20%"


@pytest.mark.parametrize("reply", [LLMResponse(text="모르겠어요"), LLMResponse(text='{"met": "yes"}')])
def test_watch_unparseable_judgement_is_not_met(user, weather_tool, monkeypatch, reply):
    from automation.services import run_due_watches

    pushed = []
    monkeypatch.setattr("automation.services.notify", lambda *a: pushed.append(a))
    watch = make_watch(user)
    run_due_watches(llm=FakeLLM([reply]))

    watch.refresh_from_db()
    assert pushed == [] and watch.is_active is True


def test_expired_watch_is_turned_off_without_running(user, weather_tool):
    from automation.services import run_due_watches

    watch = make_watch(user, expires_at=timezone.now() - timedelta(minutes=1))
    run_due_watches(llm=FakeLLM([]))

    watch.refresh_from_db()
    assert watch.is_active is False and weather_tool == []


def test_watch_skipped_when_daily_cost_limit_reached(user, weather_tool, settings):
    from decimal import Decimal

    from agent.models import UsageLog
    from automation.services import run_due_watches

    settings.DAILY_COST_LIMIT_USD = Decimal("0.01")
    UsageLog.objects.create(purpose="agent", model="x", cost_usd=Decimal("0.02"))
    make_watch(user)

    assert run_due_watches(llm=FakeLLM([])) == 0
    assert weather_tool == []


def test_watch_judge_never_sees_instructions_as_commands(user, weather_tool, monkeypatch):
    """도구 결과는 '데이터'로 감싸서 판단 모델에 넘긴다(웹 검색 결과에 지시문이 있어도)."""
    from automation.services import run_due_watches

    monkeypatch.setattr("automation.services.notify", lambda *a: None)
    make_watch(user)
    llm = FakeLLM([judge(False)])
    run_due_watches(llm=llm)

    content = llm.calls[0]["messages"][0]["content"]
    assert "외부에서 가져온 데이터" in content
    assert llm.calls[0]["tools"] == []


def test_maintenance_runs_automation(monkeypatch):
    from agent import worker

    called = []
    monkeypatch.setattr("automation.services.run_due_schedules", lambda: called.append("s") or 0)
    monkeypatch.setattr("automation.services.run_due_watches", lambda llm=None: called.append("w") or 0)
    worker.run_maintenance()
    assert called == ["s", "w"]


# ── 아침 브리핑 ─────────────────────────────────────────


def test_new_user_gets_morning_briefing():
    from django.contrib.auth import get_user_model

    from automation.models import ScheduledJob
    from automation.services import BRIEFING_PROMPT, BRIEFING_TITLE

    new_user = get_user_model().objects.create_user("newbie", password="pw-for-test-1234")
    job = ScheduledJob.objects.get(user=new_user)
    assert job.title == BRIEFING_TITLE
    assert job.time_of_day == time(7, 30)
    assert job.weekdays == []  # 매일
    assert "제안" in job.prompt_text and job.prompt_text == BRIEFING_PROMPT


def test_briefing_time_can_be_changed_in_settings(logged_in_client, user):
    from automation.models import ScheduledJob
    from automation.services import BRIEFING_TITLE

    response = logged_in_client.post("/settings/briefing/", {"time_of_day": "06:45", "is_active": "on"})

    assert response.status_code == 302
    job = ScheduledJob.objects.get(user=user, title=BRIEFING_TITLE)
    assert job.time_of_day == time(6, 45)
    assert timezone.localtime(job.next_run_at).time() == time(6, 45)


def test_briefing_can_be_turned_off(logged_in_client, user):
    from automation.models import ScheduledJob
    from automation.services import BRIEFING_TITLE

    logged_in_client.post("/settings/briefing/", {"time_of_day": "07:30"})
    assert ScheduledJob.objects.get(user=user, title=BRIEFING_TITLE).is_active is False


# ── [제안] 버튼 ─────────────────────────────────────────


def test_suggestion_lines_become_buttons(logged_in_client, user, conversation):
    from chat.models import Message

    task = Task.objects.create(user=user, conversation=conversation, status="done")
    Message.objects.create(
        conversation=conversation, role="assistant", task=task,
        content="오늘 일정은 2개입니다.\n[제안] 우산 챙기기를 할 일에 추가해줘\n[제안] 저녁 약속 장소 찾아줘",
    )  # fmt: skip
    page = logged_in_client.get(f"/c/{conversation.id}/").content.decode()

    assert "[제안]" not in page  # 줄 자체는 버튼으로 바뀐다
    assert page.count('name="content" value="') == 2
    assert 'value="우산 챙기기를 할 일에 추가해줘"' in page


def test_bold_markdown_is_rendered_but_html_is_escaped():
    from chat.templatetags.chat_extras import light_markdown

    assert light_markdown("**오늘 요약**") == "<strong>오늘 요약</strong>"
    rendered = light_markdown('<script>alert(1)</script> **<img src=x onerror="x">**')
    assert "<script>" not in rendered and "<img" not in rendered
    assert "&lt;script&gt;" in rendered


def test_orphan_suggestion_heading_is_removed():
    from chat.templatetags.chat_extras import split_suggestions

    body, suggestions = split_suggestions("날씨 맑음\n\n**💡 제안**\n[제안] 우산 챙기기 추가해줘")
    assert body == "날씨 맑음"
    assert suggestions == ["우산 챙기기 추가해줘"]
    assert split_suggestions("제안\n본문")[0] == "제안\n본문"  # 제안 버튼이 없으면 건드리지 않는다


def test_prompt_tells_model_the_suggestion_format():
    """실사용에서 발견: 형식을 알려 주지 않으니 모델이 '💡 제안' 목록으로 써서 버튼이 되지 않았다."""
    from agent.prompts import SYSTEM_PROMPT

    assert '"[제안] "으로 시작' in SYSTEM_PROMPT


def test_split_suggestions():
    from chat.templatetags.chat_extras import split_suggestions

    body, suggestions = split_suggestions("요약\n[제안] A 해줘\n본문 계속\n[제안]  B 해줘 ")
    assert body == "요약\n본문 계속"
    assert suggestions == ["A 해줘", "B 해줘"]


# ── 화면 ────────────────────────────────────────────────


def test_schedules_page_lists_toggles_and_deletes(logged_in_client, user):
    from automation.models import ScheduledJob

    job = ScheduledJob.objects.get(user=user)  # 아침 브리핑
    page = logged_in_client.get("/schedules/").content.decode()
    assert "아침 브리핑" in page and "07:30" in page

    logged_in_client.post(f"/schedules/{job.id}/toggle/")
    job.refresh_from_db()
    assert job.is_active is False

    logged_in_client.post(f"/schedules/{job.id}/delete/")
    assert not ScheduledJob.objects.filter(id=job.id).exists()


def test_watches_page_lists_toggles_and_deletes(logged_in_client, user):
    from automation.models import Watch

    watch = make_watch(user, last_result_json={"met": False, "summary": "강수 확률 20%"})
    page = logged_in_client.get("/watches/").content.decode()
    assert "비 오면 알림" in page and "강수 확률 20%" in page

    logged_in_client.post(f"/watches/{watch.id}/toggle/")
    watch.refresh_from_db()
    assert watch.is_active is False
    logged_in_client.post(f"/watches/{watch.id}/delete/")
    assert not Watch.objects.filter(id=watch.id).exists()


def test_automation_views_are_private(client, other_user, user):
    from automation.models import ScheduledJob

    job = ScheduledJob.objects.get(user=user)
    client.force_login(other_user)
    assert client.post(f"/schedules/{job.id}/toggle/").status_code == 404
    assert client.post(f"/schedules/{job.id}/delete/").status_code == 404
