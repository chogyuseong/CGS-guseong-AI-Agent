"""1단계: 내부 도구(메모, 할 일). 결과는 항상 {ok, data, error, sources, fetched_at} 형식이다."""

import pytest

pytestmark = pytest.mark.django_db

RESULT_KEYS = {"ok", "data", "error", "sources", "fetched_at"}


def run_tool(name, user, **kwargs):
    from agent.registry import get_tool, run_tool

    return run_tool(get_tool(name), user, kwargs)


def test_notes_tools_are_registered_as_expected_levels():
    from agent.registry import get_tool

    assert get_tool("save_note").risk_level == "INTERNAL"
    assert get_tool("add_todo").risk_level == "INTERNAL"
    assert get_tool("complete_todo").risk_level == "INTERNAL"
    assert get_tool("list_todos").risk_level == "READ"


def test_save_note(user):
    from tools.models import Note

    result = run_tool("save_note", user, content="주차 B2 13번")
    assert set(result) == RESULT_KEYS
    assert result["ok"] is True
    assert Note.objects.get(user=user).content == "주차 B2 13번"


def test_add_list_and_complete_todo(user):
    added = run_tool("add_todo", user, title="우유 사기")
    todo_id = added["data"]["id"]

    listed = run_tool("list_todos", user)
    assert [t["title"] for t in listed["data"]["todos"]] == ["우유 사기"]

    done = run_tool("complete_todo", user, todo_id=todo_id)
    assert done["ok"] is True
    assert run_tool("list_todos", user)["data"]["todos"] == []


def test_cannot_complete_someone_elses_todo(user, other_user):
    from tools.models import Todo

    todo = Todo.objects.create(user=other_user, title="남의 할 일")
    result = run_tool("complete_todo", user, todo_id=todo.id)

    assert result["ok"] is False
    assert result["error"]
    todo.refresh_from_db()
    assert todo.is_done is False


def test_tool_exception_becomes_ok_false(user):
    import dataclasses

    from agent.registry import get_tool, run_tool

    def boom(user, **kwargs):
        raise RuntimeError("DB 연결 끊김")

    broken = dataclasses.replace(get_tool("save_note"), run=boom)
    result = run_tool(broken, user, {"content": "x"})

    assert result["ok"] is False
    assert "DB 연결 끊김" not in result["error"]  # 내부 오류 문구를 LLM에 그대로 넘기지 않는다


def test_external_tool_cannot_run_through_normal_path(user):
    from agent.models import RiskLevel
    from agent.registry import Tool, run_tool

    fake_external = Tool(
        name="fake_external",
        description="테스트용",
        input_schema={"type": "object", "properties": {}},
        risk_level=RiskLevel.EXTERNAL,
        run=lambda user, **kwargs: {"ok": True},
        label=lambda **kwargs: "외부 행동",
    )
    with pytest.raises(PermissionError):
        run_tool(fake_external, user, {})
