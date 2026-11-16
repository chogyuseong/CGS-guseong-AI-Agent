"""4단계: web_search(NAVER API HUB 웹문서 검색).

실제 응답(tests/fixtures/naver_webkr_*.json)으로 테스트한다.
"""

import json
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from django.utils import timezone

FIXTURES = Path(__file__).parent / "fixtures"
RESULTS = json.loads((FIXTURES / "naver_webkr_seongsu.json").read_text("utf-8"))


def saved(name):
    """상태 코드와 본문을 함께 저장해 둔 실패 응답."""
    data = json.loads((FIXTURES / f"{name}.json").read_text("utf-8"))
    return data["status"], data["body"]


@pytest.fixture
def naver(monkeypatch, settings):
    settings.NAVER_CLIENT_ID = "test-id"
    settings.NAVER_CLIENT_SECRET = "test-secret"
    sent = []

    def install(*responses):
        queue = list(responses)

        def handler(request):
            sent.append(request)
            item = queue.pop(0)
            if isinstance(item, Exception):
                raise item
            status, body = item
            return httpx.Response(status, json=body)

        monkeypatch.setattr(
            "tools.http.make_client", lambda: httpx.Client(transport=httpx.MockTransport(handler))
        )
        return sent

    return install


def search(**kwargs):
    from agent.registry import get_tool, run_tool

    return run_tool(get_tool("web_search"), None, kwargs)


def test_registered_as_read_tool_with_external_content():
    from agent.registry import get_tool, validate_input

    tool = get_tool("web_search")
    assert tool.risk_level == "READ"
    # 웹 검색 결과는 남이 쓴 글이다. LLM에 넘길 때 '지시가 아니라 데이터'라는 경고로 감싼다(절대 원칙 7).
    assert tool.external_content is True
    assert validate_input(tool, {"query": "커피", "size": 11})  # LLM에 넘기는 양을 10개로 제한


def test_request_uses_api_hub_url_and_headers(naver):
    sent = naver((200, RESULTS))
    search(query="성수동 저녁 약속 맛집", size=3)

    request = sent[0]
    assert str(request.url).startswith("https://naverapihub.apigw.ntruss.com/search/v1/webkr?")
    assert request.headers["X-NCP-APIGW-API-KEY-ID"] == "test-id"
    assert request.headers["X-NCP-APIGW-API-KEY"] == "test-secret"
    assert request.url.params["query"] == "성수동 저녁 약속 맛집"
    assert request.url.params["display"] == "3"
    assert request.url.params["format"] == "json"


def test_html_tags_and_entities_are_removed(naver):
    naver((200, RESULTS))
    result = search(query="성수동 저녁 약속 맛집", size=3)

    assert result["ok"] is True
    items = result["data"]["items"]
    assert len(items) == len(RESULTS["items"])
    for item, raw in zip(items, RESULTS["items"], strict=True):
        assert "<b>" not in item["title"] and "</b>" not in item["title"]
        assert "<b>" not in item["summary"]
        assert item["link"] == raw["link"]
    assert "<b>" in RESULTS["items"][0]["title"]  # 실제 응답에는 태그가 들어 있었다


def test_entities_are_unescaped():
    from tools.websearch import clean_text

    assert clean_text("<b>A&amp;B</b> &quot;맛집&quot;") == 'A&B "맛집"'


def test_sources_are_links(naver):
    naver((200, RESULTS))
    result = search(query="성수동 맛집")

    assert result["sources"] == [item["link"] for item in RESULTS["items"]]
    assert result["fetched_at"]


def test_result_is_wrapped_as_external_data_for_llm(naver, user, conversation):
    """루프가 LLM에 돌려줄 때 외부 데이터 경고문으로 감싸는지 확인한다."""
    from agent.llm import FakeLLM, LLMResponse, ToolCall
    from agent.loop import run_task
    from agent.models import Task

    naver((200, RESULTS))
    task = Task.objects.create(user=user, conversation=conversation)
    from chat.models import Message

    Message.objects.create(conversation=conversation, role="user", content="성수동 맛집 검색해줘", task=task)
    call = LLMResponse(text="", tool_calls=[ToolCall("t1", "web_search", {"query": "성수동 맛집"})])
    llm = FakeLLM([call, LLMResponse(text="찾았어요.")])
    run_task(task.id, llm=llm)

    tool_result = llm.calls[1]["messages"][-1]["content"][0]["content"]
    assert tool_result.startswith("아래는 외부에서 가져온 데이터다.")


# ── 실패 4가지 + 실제로 받아 둔 오류 응답 ────────────────


def test_timeout_is_ok_false(naver):
    naver(httpx.ReadTimeout("느림"), httpx.ReadTimeout("느림"))
    result = search(query="커피")
    assert result["ok"] is False and "10초" in result["error"]


def test_bad_key_401_real_response_is_ok_false(naver):
    naver(saved("naver_webkr_bad_key"))
    result = search(query="커피")
    assert result["ok"] is False and "키" in result["error"]


def test_429_is_ok_false(naver):
    naver((429, {"error": {"errorCode": "429", "message": "Too Many Requests"}}))
    result = search(query="커피")
    assert result["ok"] is False and "한도" in result["error"]


def test_empty_result_real_response_is_ok_false(naver):
    naver(saved("naver_webkr_empty"))
    result = search(query="없는검색어")
    assert result["ok"] is False and "없는검색어" in result["error"]


def test_bad_parameter_400_real_response_is_ok_false(naver):
    naver(saved("naver_webkr_bad_display"))
    result = search(query="커피")
    assert result["ok"] is False and "400" in result["error"]


def test_missing_keys_fail_before_network(naver, settings):
    sent = naver((200, RESULTS))
    settings.NAVER_CLIENT_SECRET = ""
    result = search(query="커피")
    assert result["ok"] is False and "NAVER_CLIENT" in result["error"]
    assert sent == []


# ── 보관 기간: 21일이 지난 검색 결과는 기록에서 지운다 ────


@pytest.mark.django_db
def test_old_web_search_results_are_purged(user, conversation):
    from agent import worker
    from agent.models import RiskLevel, Step, Task

    task = Task.objects.create(user=user, conversation=conversation, status=Task.Status.DONE)
    old = Step.objects.create(
        task=task, order=1, tool_name="web_search", risk_level=RiskLevel.READ,
        output_json={"ok": True, "data": {"items": [{"title": "옛 결과"}]}}, ok=True, status="done",
    )  # fmt: skip
    recent = Step.objects.create(
        task=task, order=2, tool_name="web_search", risk_level=RiskLevel.READ,
        output_json={"ok": True, "data": {"items": [{"title": "최근 결과"}]}}, ok=True, status="done",
    )  # fmt: skip
    other = Step.objects.create(
        task=task, order=3, tool_name="list_todos", risk_level=RiskLevel.READ,
        output_json={"ok": True, "data": {"todos": []}}, ok=True, status="done",
    )  # fmt: skip
    Step.objects.filter(id__in=[old.id, other.id]).update(created_at=timezone.now() - timedelta(days=22))

    assert worker.purge_expired_search_results() == 1
    old.refresh_from_db()
    recent.refresh_from_db()
    other.refresh_from_db()
    assert "옛 결과" not in json.dumps(old.output_json, ensure_ascii=False)
    assert old.output_json["purged"] is True
    assert old.ok is True  # 성공했다는 사실은 남긴다
    assert "최근 결과" in json.dumps(recent.output_json, ensure_ascii=False)
    assert other.output_json == {"ok": True, "data": {"todos": []}}  # 다른 도구는 건드리지 않는다
    assert worker.purge_expired_search_results() == 0  # 이미 지운 것은 다시 세지 않는다


@pytest.mark.django_db
def test_maintenance_runs_purge(monkeypatch):
    from agent import worker

    called = []
    monkeypatch.setattr(worker, "purge_expired_search_results", lambda: called.append(1) or 0)
    worker.run_maintenance()
    assert called == [1]
