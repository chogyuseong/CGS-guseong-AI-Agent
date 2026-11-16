"""
내부 도구: 메모와 할 일. 앱 DB 안에서만 바뀌고 되돌릴 수 있어 INTERNAL, 조회는 READ.
모든 조회와 변경에 user 조건을 붙여 다른 사람의 데이터는 건드리지 못한다.
"""

from django.utils import timezone

from agent.models import RiskLevel
from agent.registry import Tool, error_result, ok_result, register
from tools.models import Note, Todo

LIST_TODOS_LIMIT = 50


def save_note(user, content):
    note = Note.objects.create(user=user, content=content.strip())
    return ok_result({"id": note.id, "saved_to": "앱 메모"})


def add_todo(user, title):
    todo = Todo.objects.create(user=user, title=title.strip())
    return ok_result({"id": todo.id, "title": todo.title, "saved_to": "앱 할 일 목록"})


def list_todos(user):
    todos = Todo.objects.filter(user=user, is_done=False).order_by("created_at")[:LIST_TODOS_LIMIT]
    return ok_result({"todos": [{"id": todo.id, "title": todo.title} for todo in todos]})


def complete_todo(user, todo_id):
    todo = Todo.objects.filter(user=user, id=todo_id).first()
    if todo is None:
        return error_result("해당 할 일을 찾지 못했습니다. list_todos로 id를 먼저 확인하세요.")
    if todo.is_done:
        return ok_result({"id": todo.id, "title": todo.title, "already_done": True})
    todo.is_done = True
    todo.done_at = timezone.now()
    todo.save(update_fields=["is_done", "done_at", "updated_at"])
    return ok_result({"id": todo.id, "title": todo.title})


def _text_input(field, description, max_length):
    # additionalProperties=False: 스키마에 없는 값을 LLM이 끼워 넣으면 실행하지 않는다.
    return {
        "type": "object",
        "properties": {
            field: {"type": "string", "minLength": 1, "maxLength": max_length, "description": description}
        },
        "required": [field],
        "additionalProperties": False,
    }


register(
    Tool(
        name="save_note",
        description=(
            "사용자가 남겨 달라고 한 메모를 앱에 저장한다. 구글 캘린더나 외부 서비스에는 저장하지 않는다."
        ),
        input_schema=_text_input("content", "저장할 메모 내용", 2000),
        risk_level=RiskLevel.INTERNAL,
        run=save_note,
        label=lambda content="", **_: f"메모 저장: {content[:20]}",
    )
)

register(
    Tool(
        name="add_todo",
        description="앱의 할 일 목록에 항목 하나를 추가한다.",
        input_schema=_text_input("title", "할 일 제목", 200),
        risk_level=RiskLevel.INTERNAL,
        run=add_todo,
        label=lambda title="", **_: f"할 일 추가: {title[:20]}",
    )
)

register(
    Tool(
        name="list_todos",
        description="앱의 할 일 목록에서 아직 끝나지 않은 항목을 id와 함께 보여 준다.",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        risk_level=RiskLevel.READ,
        run=list_todos,
        label=lambda **_: "할 일 목록 확인",
    )
)

register(
    Tool(
        name="complete_todo",
        description="할 일 하나를 완료 처리한다. id를 모르면 먼저 list_todos로 찾는다.",
        input_schema={
            "type": "object",
            "properties": {"todo_id": {"type": "integer", "description": "완료할 할 일의 id"}},
            "required": ["todo_id"],
            "additionalProperties": False,
        },
        risk_level=RiskLevel.INTERNAL,
        run=complete_todo,
        label=lambda todo_id=None, **_: f"할 일 완료 처리 (#{todo_id})",
    )
)
