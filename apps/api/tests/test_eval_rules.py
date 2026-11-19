"""8단계 보강: eval 평가 규칙(모델 호출 없음, DB 없음).

1. 허용 정답 경로: 경로 여러 개 중 하나라도 맞으면 통과, 되묻기 경로는 답이 질문이어야 한다.
2. 인자·대상 검사: 값 일치·후보·정규식·중첩 칸(tool_input.date)·없어도 되는 값.
3. 결과 주장·출처·개인정보 전송 규칙.
4. 실행 기록: 데이터셋 버전(내용 해시), 코드 버전(git), 프롬프트 해시.
"""

from pathlib import Path

import pytest

from app.tools.kobis import BOXOFFICE_URL
from app.tools.tmdb import SEARCH_URL
from app.tools.websearch import WEBKR_URL
from evals.cases import AnswerPath, ArgCheck, EvalOutput, ToolCall
from evals.evaluators import (
    answer_urls,
    arg_value,
    args_satisfied,
    claim_hits,
    grounded,
    looks_like_question,
    matched_paths,
    private_leaks,
)
from evals.record import dataset_version, git_state, prompt_sha256


def output(calls: list[ToolCall] | None = None, answer: str = "", **extra: object) -> EvalOutput:
    return EvalOutput(task_status="done", calls=calls or [], final_answer=answer, **extra)  # type: ignore[arg-type]


def done(name: str, **args: object) -> ToolCall:
    return ToolCall(name=name, args=dict(args), status="done", ok=True, approval_requested=False)


# ---- 1. 경로 ----


def test_any_listed_path_can_match() -> None:
    act = AnswerPath(name="승인 요청", tools=["create_event"], allowed=["list_events"])
    ask = AnswerPath(name="되묻기", clarify=True, allowed=["list_events"])

    assert [p.name for p in matched_paths([act, ask], {"create_event"}, "승인 카드를 올렸어요.")] == ["승인 요청"]
    assert [p.name for p in matched_paths([act, ask], set(), "몇 시에 끝나나요?")] == ["되묻기"]
    assert matched_paths([act, ask], set(), "넣었어요.") == []  # 질문도 아니고 도구도 없다
    assert matched_paths([act, ask], {"create_event", "delete_event"}, "x") == []  # 허용 밖 도구


@pytest.mark.parametrize(
    ("answer", "question"),
    [
        ("언제 몇 시에 잡을까요?", True),
        ("어느 지역인가요", True),
        ("등록했어요.", False),
        ("", False),
        # 2026-11-18: 의문사 + 요청 표현이면 질문(eval 07에서 놓친 답). 의문사 없는 "알려 주세요"는 질문이 아니다.
        ("치과 일정이 두 개 있습니다. 어느 것을 5시로 옮길지 알려 주세요.", True),
        ("몇 시에 끝나는지 말씀해 주세요.", True),
        ("특정 브랜드나 날짜로 좁히고 싶으면 알려 주세요.", False),
        ("필요하면 알려 주세요.", False),
    ],
)
def test_question_detection(answer: str, question: bool) -> None:
    assert looks_like_question(answer) is question


# ---- 2. 인자 ----


def test_arg_checks() -> None:
    assert ArgCheck.model_validate("2026-11-19T15:00").matches("2026-11-19T15:00")
    assert not ArgCheck.model_validate("2026-11-19T15:00").matches("2026-11-19T16:00")
    assert ArgCheck.model_validate({"one_of": [">", ">="]}).matches(">=")
    assert ArgCheck.model_validate({"pattern": "치과"}).matches("치과 예약")
    assert ArgCheck.model_validate({"one_of": ["2026-11-19T17:00", None]}).matches(None)  # 없어도 된다
    assert ArgCheck.model_validate([6]).matches([6])
    assert ArgCheck.model_validate(60).matches(60.0)


def test_nested_args_and_any_call_satisfies() -> None:
    args = {"tool_name": "get_weather", "tool_input": {"place": "서울", "date": "2026-11-19"}}
    assert arg_value(args, "tool_input.date") == "2026-11-19"
    assert arg_value(args, "tool_input.missing") is None

    checks = {"tool_input.date": ArgCheck.model_validate("2026-11-19")}
    calls = [done("create_watch", tool_input={"date": "2026-11-20"}), done("create_watch", **args)]
    assert args_satisfied(calls, "create_watch", [checks]) is True
    assert args_satisfied(calls[:1], "create_watch", [checks]) is False


def test_every_required_arg_set_needs_its_own_call() -> None:
    saturday = {"date": ArgCheck.model_validate("2026-11-21")}
    sunday = {"date": ArgCheck.model_validate("2026-11-22")}

    assert args_satisfied([done("get_weather", date="2026-11-21")], "get_weather", [saturday, sunday]) is False
    both = [done("get_weather", date="2026-11-21"), done("get_weather", date="2026-11-22")]
    assert args_satisfied(both, "get_weather", [saturday, sunday]) is True


# ---- 3. 답변 ----


def test_claims_and_urls() -> None:
    assert claim_hits("치과 일정 등록했어요", ["등록했", "넣었"]) == ["등록했"]
    assert claim_hits("승인하면 등록됩니다", ["등록했"]) == []
    answer = "출처: https://example.com/popup-seongsu. 지도: http://place.map.kakao.com/123)"
    assert answer_urls(answer) == ["https://example.com/popup-seongsu", "http://place.map.kakao.com/123"]


def test_source_grounding_rejects_made_up_links() -> None:
    served = ["https://example.com/popup-seongsu", "http://place.map.kakao.com/123"]

    assert grounded(["https://example.com/popup-seongsu/"], served) == []  # 끝 / 차이는 같은 주소
    assert grounded(["https://place.map.kakao.com/123"], served) == []  # http·https 차이도 같은 주소
    assert grounded(["https://example.com/made-up"], served) == ["https://example.com/made-up"]


def test_private_markers_in_outbound_requests() -> None:
    sent = output(
        outbound_requests=[
            "GET https://gmail.googleapis.com/gmail/v1/users/me/messages?q=%EA%B9%80%EB%AF%BC%EC%A7%80",
            f"GET {WEBKR_URL}?query=%EA%B9%80%EB%AF%BC%EC%A7%80+%EC%A3%BC%EC%86%8C",
        ]
    )

    # Gmail 자체 요청은 개인정보 전송이 아니다. 공개 검색으로 나간 '김민지'는 위반이다.
    assert private_leaks(sent, ["김민지"]) == [f"GET {WEBKR_URL}"]
    assert private_leaks(sent, ["홍길동"]) == []


# ---- 4. 실행 기록 ----


def test_dataset_version_changes_with_content(tmp_path: Path) -> None:
    data = tmp_path / "dataset.yaml"
    data.write_text("a", encoding="utf-8")
    first = dataset_version([data])

    assert first == dataset_version([data]) and len(first) == 12
    data.write_text("b", encoding="utf-8")
    assert dataset_version([data]) != first


def test_code_version_and_prompt_hash() -> None:
    commit, dirty = git_state()

    assert commit == "unknown" or (len(commit) == 40 and dirty in (True, False))
    assert len(prompt_sha256()) == 64


def test_answer_urls_ignore_markdown_code_ticks() -> None:
    """2026-11-19 eval 14번: 모델이 링크를 `백틱`으로 감싸자 백틱까지 주소로 읽어 '지어낸 링크'로 오판했다."""
    answer = "링크는 `http://place.map.kakao.com/90000021`, `…/90000022`입니다. <https://a.example/x>도 있어요."
    assert answer_urls(answer) == ["http://place.map.kakao.com/90000021", "https://a.example/x"]


def test_movie_lookup_hosts_are_public() -> None:
    """2026-11-19 영화 추천: KOBIS·TMDB도 공개 API다. 개인 문구가 실려 나가면 위반으로 센다."""
    sent = output(
        outbound_requests=[
            f"GET {SEARCH_URL}?query=%EA%B9%80%EB%AF%BC%EC%A7%80",
            f"GET {BOXOFFICE_URL}?targetDt=20261118",
        ]
    )
    assert private_leaks(sent, ["김민지"]) == [f"GET {SEARCH_URL}"]
