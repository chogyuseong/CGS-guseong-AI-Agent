"""4단계: Google Calendar 도구. 실제 응답(tests/fixtures/gcal_*.json, 이메일은 지움)으로 테스트한다.

테스트 일정: 2027-01-15(금) 19:00~21:00 '[마이에이전트 테스트] 저녁 약속', 장소 '서울 성동구 성수동'.
"""

import json
from pathlib import Path

import httpx
import pytest

FIXTURES = Path(__file__).parent / "fixtures"
EVENTS = "/calendar/v3/calendars/primary/events"


def fixture(name):
    return json.loads((FIXTURES / f"gcal_{name}.json").read_text("utf-8"))


INSERT, PATCH, LIST, GET = fixture("insert"), fixture("patch"), fixture("list"), fixture("get")
EVENT_ID = INSERT["id"]


@pytest.fixture
def calendar(monkeypatch):
    """구글 대신 (메서드, 경로)별로 정한 응답을 돌려준다. 보낸 요청을 모은다."""
    monkeypatch.setattr("accounts.google.access_token", lambda user: "test-access-token")
    sent = []

    def install(routes):
        def handler(request):
            sent.append(request)
            reply = routes[(request.method, request.url.path)]
            if isinstance(reply, Exception):
                raise reply
            status, body = reply
            return httpx.Response(status, json=body) if body is not None else httpx.Response(status)

        monkeypatch.setattr(
            "tools.http.make_client", lambda: httpx.Client(transport=httpx.MockTransport(handler))
        )
        return sent

    return install


def run(name, **kwargs):
    from agent.registry import get_tool, run_tool, validate_input

    tool = get_tool(name)
    assert validate_input(tool, kwargs) is None
    if tool.risk_level == "EXTERNAL":
        return tool.run(None, **kwargs)  # 승인 게이트는 test_approvals가 검증한다. 여기서는 도구 동작만 본다
    return run_tool(tool, None, kwargs)


def preview(name, **kwargs):
    from agent.registry import get_tool

    return get_tool(name).preview(None, **kwargs)


# ── 등록 ─────────────────────────────────────────────────


def test_risk_levels():
    from agent.registry import get_tool

    assert get_tool("list_events").risk_level == "READ"
    assert get_tool("list_events").external_content is True  # 남이 보낸 초대의 제목도 데이터로 다룬다
    for name in ("create_event", "update_event", "delete_event"):
        tool = get_tool(name)
        assert tool.risk_level == "EXTERNAL"
        assert tool.preview is not None


def test_time_format_is_validated_by_schema():
    from agent.registry import get_tool, validate_input

    tool = get_tool("create_event")
    assert validate_input(tool, {"title": "약속", "start": "금요일 7시", "end": "2027-01-15T21:00"})
    assert (
        validate_input(tool, {"title": "약속", "start": "2027-01-15T19:00", "end": "2027-01-15T21:00"})
        is None
    )


# ── list_events (READ) ──────────────────────────────────


def test_list_events_sends_seoul_day_range(calendar):
    sent = calendar({("GET", EVENTS): (200, LIST)})
    result = run("list_events", date_from="2027-01-15", date_to="2027-01-15")

    params = sent[0].url.params
    assert params["timeMin"] == "2027-01-15T00:00:00+09:00"
    assert params["timeMax"] == "2027-01-16T00:00:00+09:00"  # 종료일 하루 전체를 포함한다
    assert params["singleEvents"] == "true" and params["orderBy"] == "startTime"
    assert sent[0].headers["Authorization"] == "Bearer test-access-token"
    assert result["ok"] is True


def test_list_events_fields_from_real_response(calendar):
    calendar({("GET", EVENTS): (200, LIST)})
    result = run("list_events", date_from="2027-01-15", date_to="2027-01-15")

    event = result["data"]["events"][0]
    raw = LIST["items"][0]
    assert event == {
        "id": raw["id"],
        "title": raw["summary"],
        "start": raw["start"]["dateTime"],
        "end": raw["end"]["dateTime"],
        "all_day": False,
        "location": raw["location"],
        "link": raw["htmlLink"],
    }
    assert result["sources"] == [raw["htmlLink"]]


def test_no_events_is_a_normal_answer(calendar):
    calendar({("GET", EVENTS): (200, fixture("list_empty"))})
    result = run("list_events", date_from="2027-01-17", date_to="2027-01-17")

    assert result["ok"] is True  # 일정이 없는 날은 실패가 아니다
    assert result["data"]["events"] == []


def test_list_range_over_31_days_fails_without_calling(calendar):
    sent = calendar({})
    result = run("list_events", date_from="2027-01-01", date_to="2027-03-01")

    assert result["ok"] is False
    assert sent == []


def test_list_failures(calendar):
    calendar({("GET", EVENTS): httpx.ReadTimeout("느림")})
    assert run("list_events", date_from="2027-01-15", date_to="2027-01-15")["ok"] is False

    calendar({("GET", EVENTS): (401, {"error": {"code": 401}})})
    assert "인증" in run("list_events", date_from="2027-01-15", date_to="2027-01-15")["error"]

    calendar({("GET", EVENTS): (429, {"error": {"code": 429}})})
    assert "한도" in run("list_events", date_from="2027-01-15", date_to="2027-01-15")["error"]


def test_not_connected_is_ok_false(monkeypatch):
    from agent.registry import get_tool, run_tool
    from tools.http import ExternalAPIError

    def not_connected(user):
        raise ExternalAPIError("Google 계정이 연결되어 있지 않습니다.")

    monkeypatch.setattr("accounts.google.access_token", not_connected)
    result = run_tool(get_tool("list_events"), None, {"date_from": "2027-01-15", "date_to": "2027-01-15"})
    assert result["ok"] is False and "연결" in result["error"]


# ── create_event (EXTERNAL) ─────────────────────────────


def test_create_sends_seoul_times(calendar):
    sent = calendar({("POST", EVENTS): (200, INSERT)})
    result = run(
        "create_event", title="저녁 약속", start="2027-01-15T19:00", end="2027-01-15T21:00", location="성수동"
    )

    body = json.loads(sent[0].content)
    assert body == {
        "summary": "저녁 약속",
        "start": {"dateTime": "2027-01-15T19:00:00", "timeZone": "Asia/Seoul"},
        "end": {"dateTime": "2027-01-15T21:00:00", "timeZone": "Asia/Seoul"},
        "location": "성수동",
    }
    assert result["ok"] is True
    assert result["data"]["id"] == EVENT_ID
    assert result["data"]["saved_to"] == "Google 캘린더"
    assert result["sources"] == [INSERT["htmlLink"]]


def test_create_end_before_start_fails_without_calling(calendar):
    sent = calendar({})
    result = run("create_event", title="약속", start="2027-01-15T21:00", end="2027-01-15T19:00")
    assert result["ok"] is False and sent == []


def test_create_timeout_is_not_retried(calendar):
    """일정 생성은 타임아웃이 나도 다시 보내지 않는다. 이미 만들어졌을 수 있다."""
    sent = calendar({("POST", EVENTS): httpx.ReadTimeout("느림")})
    result = run("create_event", title="약속", start="2027-01-15T19:00", end="2027-01-15T21:00")

    assert result["ok"] is False
    assert len(sent) == 1


def test_preview_create_has_date_weekday_times_title_calendar(calendar):
    calendar({("GET", EVENTS): (200, LIST)})
    text = preview(
        "create_event", title="저녁 약속", start="2027-01-15T19:00", end="2027-01-15T21:00", location="성수동"
    )

    assert text.startswith("1월 15일(금) 19:00~21:00 '저녁 약속' 일정을 ")
    assert LIST["summary"] in text  # 캘린더 이름
    assert "성수동" in text


def test_preview_create_falls_back_when_calendar_name_unavailable(calendar):
    calendar({("GET", EVENTS): (500, {})})
    text = preview("create_event", title="약속", start="2027-01-15T19:00", end="2027-01-15T21:00")
    assert "기본 캘린더" in text


# ── update_event (EXTERNAL) ─────────────────────────────


def test_update_title_only(calendar):
    sent = calendar({("PATCH", f"{EVENTS}/{EVENT_ID}"): (200, PATCH)})
    result = run("update_event", event_id=EVENT_ID, title=PATCH["summary"])

    assert json.loads(sent[0].content) == {"summary": PATCH["summary"]}
    assert result["ok"] is True and result["data"]["title"] == PATCH["summary"]


def test_update_start_only_keeps_end_and_checks_order(calendar):
    sent = calendar(
        {("GET", f"{EVENTS}/{EVENT_ID}"): (200, GET), ("PATCH", f"{EVENTS}/{EVENT_ID}"): (200, PATCH)}
    )
    run("update_event", event_id=EVENT_ID, start="2027-01-15T20:00")

    body = json.loads(sent[1].content)
    assert body["start"]["dateTime"] == "2027-01-15T20:00:00"
    assert body["end"]["dateTime"] == "2027-01-15T21:00:00"  # 기존 끝 시각을 유지한다


def test_update_with_nothing_to_change_fails(calendar):
    sent = calendar({})
    result = run("update_event", event_id=EVENT_ID)
    assert result["ok"] is False and sent == []


def test_preview_update_shows_before_and_after(calendar):
    calendar({("GET", f"{EVENTS}/{EVENT_ID}"): (200, GET)})
    text = preview(
        "update_event", event_id=EVENT_ID, title="회식", start="2027-01-15T20:00", end="2027-01-15T22:00"
    )

    assert GET["summary"] in text and "'회식'" in text
    assert "1월 15일(금) 19:00~21:00 → 1월 15일(금) 20:00~22:00" in text


# ── delete_event (EXTERNAL) ─────────────────────────────


def test_delete_success_has_empty_body(calendar):
    calendar({("DELETE", f"{EVENTS}/{EVENT_ID}"): (204, None)})
    result = run("delete_event", event_id=EVENT_ID)
    assert result["ok"] is True and result["data"]["deleted_id"] == EVENT_ID


def test_delete_again_410_real_response_means_not_found(calendar):
    status = fixture("delete_again")["status"]
    calendar({("DELETE", f"{EVENTS}/{EVENT_ID}"): (status, {"error": {"code": status}})})
    result = run("delete_event", event_id=EVENT_ID)

    assert status == 410  # 실제로 받아 본 값
    assert result["ok"] is False and "찾지 못했습니다" in result["error"]


def test_preview_delete_shows_title_and_time(calendar):
    calendar({("GET", f"{EVENTS}/{EVENT_ID}"): (200, GET), ("GET", EVENTS): (200, LIST)})
    text = preview("delete_event", event_id=EVENT_ID)

    assert text == f"'{GET['summary']}' (1월 15일(금) 19:00~21:00) 일정을 {LIST['summary']}에서 삭제합니다."


def test_preview_of_already_deleted_event_refuses(calendar):
    """실제로 확인: 지운 일정을 조회하면 404가 아니라 200 + status 'cancelled'가 온다."""
    from agent.registry import get_tool, preview_text
    from tools.http import ExternalAPIError

    cancelled = fixture("get_after_delete")
    assert cancelled["status"] == 200 and cancelled["body"]["status"] == "cancelled"
    calendar({("GET", f"{EVENTS}/{EVENT_ID}"): (200, cancelled["body"]), ("GET", EVENTS): (200, LIST)})

    with pytest.raises(ExternalAPIError, match="찾지 못했습니다"):
        preview("delete_event", event_id=EVENT_ID)
    text = preview_text(get_tool("delete_event"), None, {"event_id": EVENT_ID})  # 승인 카드에는 대체 문구
    assert "삭제합니다" not in text
