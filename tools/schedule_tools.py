"""
자동화 도구: 예약 작업과 조건 감시를 만들고, 보고, 지운다. 만들기·지우기는 INTERNAL, 목록은 READ.

만드는 일 자체는 앱 안에서만 바뀌므로 INTERNAL이다. 실제로 돌 때의 안전은 automation.services가 지킨다
(예약 작업은 READ 도구만, 조건 감시는 Task를 만들지 않음).
"""

from datetime import time

from agent.models import RiskLevel
from agent.registry import Tool, error_result, ok_result, register
from automation import services
from automation.models import ScheduledJob, Watch
from automation.services import AutomationError, describe_repeat

TIME_PATTERN = r"^([01]\d|2[0-3]):[0-5]\d$"


def create_schedule(user, title, prompt_text, time_of_day, weekdays):
    try:
        job = services.create_schedule(user, title, prompt_text, time.fromisoformat(time_of_day), weekdays)
    except AutomationError as error:
        return error_result(str(error))
    return ok_result(
        {
            "id": job.id,
            "title": job.title,
            "repeat": describe_repeat(job.time_of_day, job.weekdays),
            "next_run_at": job.next_run_at.isoformat(),
            "saved_to": "앱 자동화(예약 작업). 결과는 '예약 작업' 대화방에 남는다",
        }
    )


def list_schedules(user):
    jobs = ScheduledJob.objects.filter(user=user).order_by("time_of_day")
    return ok_result(
        {
            "schedules": [
                {
                    "id": job.id,
                    "title": job.title,
                    "repeat": describe_repeat(job.time_of_day, job.weekdays),
                    "is_active": job.is_active,
                }
                for job in jobs
            ]
        }
    )


def delete_schedule(user, id):  # noqa: A002 - 명세의 도구 인자 이름을 그대로 쓴다
    deleted, _ = ScheduledJob.objects.filter(user=user, id=id).delete()
    if not deleted:
        return error_result("해당 예약 작업을 찾지 못했습니다. list_schedules로 id를 확인하세요.")
    return ok_result({"deleted_id": id})


def create_watch(user, description, tool_name, tool_input, condition_text, interval_minutes, repeat=False):
    try:
        watch = services.create_watch(
            user, description, tool_name, tool_input, condition_text, interval_minutes, repeat
        )
    except AutomationError as error:
        return error_result(str(error))
    return ok_result(
        {
            "id": watch.id,
            "description": watch.description,
            "interval": f"{watch.interval_minutes}분마다 확인",
            "expires_at": f"{watch.expires_at:%Y-%m-%d} 만료",
            "repeat": "조건이 맞을 때마다(알린 뒤 12시간 쉼)" if watch.repeat else "한 번 알리고 끝",
            "saved_to": "앱 자동화(조건 감시). 조건이 맞으면 '조건 감시' 대화방과 푸시로 알린다",
        }
    )


def list_watches(user):
    watches = Watch.objects.filter(user=user).order_by("-created_at")
    return ok_result(
        {
            "watches": [
                {
                    "id": w.id,
                    "description": w.description,
                    "interval_minutes": w.interval_minutes,
                    "is_active": w.is_active,
                    "expires_at": f"{w.expires_at:%Y-%m-%d}",
                }
                for w in watches
            ]
        }
    )


def delete_watch(user, id):  # noqa: A002
    deleted, _ = Watch.objects.filter(user=user, id=id).delete()
    if not deleted:
        return error_result("해당 조건 감시를 찾지 못했습니다. list_watches로 id를 확인하세요.")
    return ok_result({"deleted_id": id})


_ID = {
    "type": "object",
    "properties": {"id": {"type": "integer"}},
    "required": ["id"],
    "additionalProperties": False,
}
_EMPTY = {"type": "object", "properties": {}, "additionalProperties": False}

register(
    Tool(
        name="create_schedule",
        description=(
            "'매일/매주 ~해줘' 요청을 예약 작업으로 등록한다. "
            "정한 시각에 prompt_text를 조회 도구만으로 실행한다. "
            "weekdays는 0=월 ... 6=일, 비우면 매일. 등록 후 주기를 사용자에게 말한다."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "title": {"type": "string", "minLength": 1, "maxLength": 100},
                "prompt_text": {"type": "string", "minLength": 1, "maxLength": 1000},
                "time_of_day": {"type": "string", "pattern": TIME_PATTERN, "description": "HH:MM, 서울 기준"},
                "weekdays": {"type": "array", "items": {"type": "integer", "minimum": 0, "maximum": 6}},
            },
            "required": ["title", "prompt_text", "time_of_day", "weekdays"],
            "additionalProperties": False,
        },
        risk_level=RiskLevel.INTERNAL,
        run=create_schedule,
        label=lambda title="", **_: f"예약 작업 등록: {title}",
    )
)
register(
    Tool(
        name="list_schedules",
        description="예약 작업 목록을 보여 준다.",
        input_schema=_EMPTY,
        risk_level=RiskLevel.READ,
        run=list_schedules,
        label=lambda **_: "예약 작업 확인",
    )
)
register(
    Tool(
        name="delete_schedule",
        description="예약 작업 하나를 지운다.",
        input_schema=_ID,
        risk_level=RiskLevel.INTERNAL,
        run=delete_schedule,
        label=lambda **_: "예약 작업 삭제",
    )
)
register(
    Tool(
        name="create_watch",
        description=(
            "'~하면 알려줘' 요청을 조건 감시로 등록한다. "
            "tool_name의 조회 도구를 tool_input으로 주기적으로 실행해 condition_text가 맞으면 알린다. "
            "쓸 수 있는 도구: get_weather, search_places, web_search, list_todos. "
            "간격은 30분 이상, 30일 뒤 만료. 등록 후 주기와 만료일을 사용자에게 말한다."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "description": {"type": "string", "minLength": 1, "maxLength": 200},
                "tool_name": {"type": "string"},
                "tool_input": {"type": "object"},
                "condition_text": {"type": "string", "minLength": 1, "maxLength": 500},
                "interval_minutes": {"type": "integer", "minimum": 30, "maximum": 1440},
                "repeat": {"type": "boolean", "default": False},
            },
            "required": ["description", "tool_name", "tool_input", "condition_text", "interval_minutes"],
            "additionalProperties": False,
        },
        risk_level=RiskLevel.INTERNAL,
        run=create_watch,
        label=lambda description="", **_: f"조건 감시 등록: {description[:20]}",
    )
)
register(
    Tool(
        name="list_watches",
        description="조건 감시 목록을 보여 준다.",
        input_schema=_EMPTY,
        risk_level=RiskLevel.READ,
        run=list_watches,
        label=lambda **_: "조건 감시 확인",
    )
)
register(
    Tool(
        name="delete_watch",
        description="조건 감시 하나를 지운다.",
        input_schema=_ID,
        risk_level=RiskLevel.INTERNAL,
        run=delete_watch,
        label=lambda **_: "조건 감시 삭제",
    )
)
