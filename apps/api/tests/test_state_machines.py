"""9단계 보강: docs/STATE_MACHINES.md와 코드가 어긋나지 않는지 가볍게 확인한다.

1. 각 절의 "상태 목록(Enum이름): ..." 줄이 그 enum의 값 전부와 같다(상태를 더하거나 빼면 문서도 고쳐야 한다).
2. 전이 표의 첫 칸에 쓴 상태(`값`)가 그 절의 enum에 있다.
3. "누가" 칸의 `모듈.함수`가 실제로 있다(함수 이름을 바꾸면 문서도 고쳐야 한다).
"""

import importlib
import re
from enum import StrEnum
from pathlib import Path

import pytest

from app.models import enums

DOC = Path(__file__).resolve().parents[3] / "docs" / "STATE_MACHINES.md"
MODULES = {
    "runner": "app.agent.runner",
    "approvals": "app.agent.approvals",
    "cancel": "app.agent.cancel",
    "executor": "app.agent.executor",
    "outbox": "app.agent.outbox",
    "tracking": "app.agent.tracking",
    "google_auth": "app.google_auth",
    "check": "app.automation.check",
    "conversations": "app.routers.conversations",
}
STATE_LIST = re.compile(r"^상태 목록\((\w+)\): (.+)$", re.MULTILINE)
SECTION = re.compile(r"^## .*\(`(\w+)`\)\s*$", re.MULTILINE)
CODE = re.compile(r"`([\w.]+)`")


def doc_text() -> str:
    return DOC.read_text(encoding="utf-8")


def sections() -> dict[str, str]:
    """enum 이름 → 그 절의 본문(다음 '## ' 제목 전까지)."""
    text = doc_text()
    found = list(SECTION.finditer(text))
    result = {}
    for match in found:
        end = text.find("\n## ", match.end())
        result[match.group(1)] = text[match.end() : end if end != -1 else len(text)]
    return result


def table_rows(body: str) -> list[list[str]]:
    rows = []
    for line in body.splitlines():
        if line.startswith("| ") and "→" in line.split("|")[1]:
            rows.append([cell.strip() for cell in line.strip().strip("|").split("|")])
    return rows


def enum_values(name: str) -> set[str]:
    enum_class: type[StrEnum] = getattr(enums, name)
    return {member.value for member in enum_class}


def test_state_lists_match_enums() -> None:
    lists = {name: [s.strip() for s in states.split(",")] for name, states in STATE_LIST.findall(doc_text())}

    assert {"TaskStatus", "OperationStatus", "ApprovalStatus", "OutboxStatus", "StepStatus"} <= set(lists)
    for name, states in lists.items():
        assert len(states) == len(set(states)), name
        assert set(states) == enum_values(name), name
    assert len(lists["TaskStatus"]) == 12 and len(lists["OperationStatus"]) == 12  # BUILD_SPEC 9.2·4.2


@pytest.mark.parametrize("name", ["TaskStatus", "OperationStatus", "ApprovalStatus", "OutboxStatus", "StepStatus"])
def test_transition_tables_use_known_states(name: str) -> None:
    rows = table_rows(sections()[name])

    assert rows, name
    for row in rows:
        used = set(CODE.findall(row[0]))
        assert used <= enum_values(name), (name, row[0])


def test_transition_owners_exist() -> None:
    missing, checked = [], 0
    for body in sections().values():
        for row in table_rows(body):
            for ref in CODE.findall(row[1]):
                checked += 1
                module_name, _, function = ref.partition(".")
                if module_name not in MODULES or not hasattr(importlib.import_module(MODULES[module_name]), function):
                    missing.append(ref)

    assert checked > 30  # 표를 실제로 읽었다
    assert missing == []


def test_every_used_task_state_has_a_transition() -> None:
    """쓰지 않는 상태(문서에 따로 적은 3개)를 빼면 모든 task 상태가 전이 표에 나온다."""
    used = {state for row in table_rows(sections()["TaskStatus"]) for state in CODE.findall(row[0])}
    unused = {"waiting_input", "reconciling", "partially_done"}

    assert used | unused == enum_values("TaskStatus")
    assert not used & unused
