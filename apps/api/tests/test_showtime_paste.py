"""붙여 넣은 시간표 검증(2026-11-19, 영화 비서 C 보강): 사용자가 실제로 준 극장·영화·시각 조합만 회차로 쓴다.

예전 검사(showtimes.not_in_paste)는 시각·상영 시간 숫자가 최근 메시지 어딘가에 있는지만 봤다. 그래서 다른 극장 줄의
시각(행 섞임), '19:30' 안의 '9:30'(오전·오후 혼동), 다른 회차의 상영 시간, 식사·귀가 문장 속 숫자도 통과했다.
가짜 모델로 compare_showtimes를 부르고, 실패한 요청은 카카오·TMAP 호출 0건·추천·예매 링크 0건인지 본다.
"""

import pytest

from tests.test_showtimes import BASE, compare, keys, run_chat  # noqa: F401  (keys는 pytest 픽스처)

SYN = (
    "금요일 7시에 강남역에서 만나서 밥 60분 먹고 볼 거야. 23:30까지 집(잠실역)에 도착해야 해.\n"
    "CGV 강남 | 영화 A | 19:30 | 110분\n"
    "메가박스 코엑스 | 영화 B | 20:10 | 95분\n"
    "식당에서 극장까지 15분, 극장에서 집까지 40분."
)
COND = {"meet_at": "2026-11-20T19:00", "deadline": "2026-11-20T23:30"}
GIVEN = {"to_theater_minutes": 15, "home_minutes": 40}
A_OK = {"theater": "CGV 강남", "movie": "영화 A", "start": "2026-11-20T19:30", "runtime_minutes": 110, **GIVEN}
B_OK = {"theater": "메가박스 코엑스", "movie": "영화 B", "start": "2026-11-20T20:10", "runtime_minutes": 95, **GIVEN}


async def check(client, agent, conversation, fake_http, message: str, *showings: dict) -> dict:
    task = await run_chat(client, agent, conversation, compare(*showings, **COND), message=message)
    [step] = task["steps"]
    return step


def assert_refused(step: dict, fake_http, *words: str) -> None:
    """거절: 경로 조회 전에 끝나 외부 요청 0건, 추천·예매 링크 없음, 문구에 모호한 항목이 있다."""
    assert not step["ok"]
    assert step["output"]["data"] is None  # 결론·예매 링크를 만들지 않았다
    assert fake_http.requests == []  # 카카오·TMAP 0건
    for word in words:
        assert word in step["output"]["user_message"]


# ---- 과제 1~6 ----


async def test_1_exact_rows_pass(client, agent, conversation, keys, fake_http):  # noqa: F811
    step = await check(client, agent, conversation, fake_http, SYN, A_OK, B_OK)
    assert step["ok"]
    assert fake_http.requests == []  # 이동 시간을 말했으므로 외부 요청 없이 끝난다


async def test_2_time_from_other_row_is_refused(client, agent, conversation, keys, fake_http):  # noqa: F811
    mixed = {**A_OK, "start": "2026-11-20T20:10"}  # 20:10은 메가박스 코엑스 영화 B 줄의 시각
    step = await check(client, agent, conversation, fake_http, SYN, mixed)
    assert_refused(step, fake_http, "20:10", "메가박스 코엑스")


async def test_3_morning_misread_is_refused(client, agent, conversation, keys, fake_http):  # noqa: F811
    morning = {**A_OK, "start": "2026-11-20T09:30"}  # '19:30' 안의 '9:30'
    step = await check(client, agent, conversation, fake_http, SYN, morning)
    assert_refused(step, fake_http, "09:30")


async def test_4_runtime_or_end_from_other_row_is_refused(client, agent, conversation, keys, fake_http):  # noqa: F811
    borrowed_runtime = {**A_OK, "runtime_minutes": 95}  # 95분은 영화 B 줄
    step = await check(client, agent, conversation, fake_http, SYN, borrowed_runtime)
    assert_refused(step, fake_http, "상영 시간", "95")


async def test_4_end_from_other_row_is_refused(client, agent, conversation, keys, fake_http):  # noqa: F811
    message = SYN + "\nCGV 강남 | 영화 C | 20:40~22:10"
    borrowed_end = {**A_OK, "runtime_minutes": None, "end": "2026-11-20T22:10"}  # 22:10은 영화 C의 종료
    step = await check(client, agent, conversation, fake_http, message, borrowed_end)
    assert_refused(step, fake_http, "종료", "22:10")


async def test_5_same_time_same_movie_in_two_theaters(client, agent, conversation, keys, fake_http):  # noqa: F811
    message = "CGV 강남 | 영화 A | 19:30 | 110분\n메가박스 코엑스 | 영화 A | 19:30 | 115분"
    mega = {**A_OK, "theater": "메가박스 코엑스", "runtime_minutes": 115}
    step = await check(client, agent, conversation, fake_http, message, A_OK, mega)
    assert step["ok"]


async def test_5_runtime_must_come_from_own_theater(client, agent, conversation, keys, fake_http):  # noqa: F811
    message = "CGV 강남 | 영화 A | 19:30 | 110분\n메가박스 코엑스 | 영화 A | 19:30 | 115분"
    step = await check(client, agent, conversation, fake_http, message, {**A_OK, "runtime_minutes": 115})
    assert_refused(step, fake_http, "상영 시간")


async def test_6_deadline_number_is_not_a_showing(client, agent, conversation, keys, fake_http):  # noqa: F811
    from_deadline = {**A_OK, "start": "2026-11-20T23:30"}  # '23:30까지 집에' 문장의 숫자
    step = await check(client, agent, conversation, fake_http, SYN, from_deadline)
    assert_refused(step, fake_http, "23:30")


async def test_6_dinner_minutes_are_not_a_runtime(client, agent, conversation, keys, fake_http):  # noqa: F811
    from_dinner = {**A_OK, "runtime_minutes": 60}  # '밥 60분' 문장의 숫자
    step = await check(client, agent, conversation, fake_http, SYN, from_dinner)
    assert_refused(step, fake_http, "상영 시간")


# ---- 형식 ----

HEADERS = "CGV 강남\n영화 A (110분)\n19:30  21:50\n메가박스 코엑스\n영화 B 95분\n20:10"


async def test_header_format_with_several_times_passes(client, agent, conversation, keys, fake_http):  # noqa: F811
    late = {**A_OK, "start": "2026-11-20T21:50"}
    step = await check(client, agent, conversation, fake_http, HEADERS, late, B_OK)
    assert step["ok"]


async def test_header_format_keeps_theater_blocks_apart(client, agent, conversation, keys, fake_http):  # noqa: F811
    step = await check(client, agent, conversation, fake_http, HEADERS, {**A_OK, "start": "2026-11-20T20:10"})
    assert_refused(step, fake_http, "20:10")


async def test_unknown_movie_header_does_not_inherit(client, agent, conversation, keys, fake_http):  # noqa: F811
    message = "CGV 강남\n영화 A 110분\n19:30\n영화 C 120분\n20:10"  # 20:10은 영화 C 회차
    step = await check(client, agent, conversation, fake_http, message, {**A_OK, "start": "2026-11-20T20:10"})
    assert_refused(step, fake_http, "20:10")


async def test_afternoon_marker_is_understood(client, agent, conversation, keys, fake_http):  # noqa: F811
    message = "CGV 강남 영화 A 오후 7:30 (110분)"
    step = await check(client, agent, conversation, fake_http, message, A_OK)
    assert step["ok"]


async def test_bare_twelve_hour_time_asks(client, agent, conversation, keys, fake_http):  # noqa: F811
    message = "CGV 강남 영화 A 7:30 (110분)"
    step = await check(client, agent, conversation, fake_http, message, A_OK)
    assert_refused(step, fake_http, "7:30", "오전인지 오후인지")


@pytest.mark.parametrize(
    ("line", "theater", "movie", "ok"),
    [
        ("CGV강남 | 영화A | 19:30 | 110분", "CGV 강남", "영화 A", True),  # 띄어쓰기 차이는 같은 지점·작품
        ("CGV 강남역 | 영화 A | 19:30 | 110분", "CGV 강남", "영화 A", False),  # 다른 지점을 합치지 않는다
        ("CGV 강남 | 영화 A2 | 19:30 | 110분", "CGV 강남", "영화 A", False),  # 다른 작품을 합치지 않는다
    ],
)
async def test_name_normalization_does_not_merge(
    client,
    agent,
    conversation,
    keys,  # noqa: F811
    fake_http,
    line,
    theater,
    movie,
    ok,
):
    step = await check(client, agent, conversation, fake_http, line, {**A_OK, "theater": theater, "movie": movie})
    assert step["ok"] is ok
    if not ok:
        assert_refused(step, fake_http)


async def test_time_without_theater_asks(client, agent, conversation, keys, fake_http):  # noqa: F811
    message = "영화 A 110분\n19:30"  # 극장이 없다
    step = await check(client, agent, conversation, fake_http, message, A_OK)
    assert_refused(step, fake_http, "19:30", "극장")
