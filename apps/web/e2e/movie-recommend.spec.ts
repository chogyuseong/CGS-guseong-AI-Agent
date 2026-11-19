// 2026-11-19 영화 비서 C 둘째 단계. 모델·KOBIS·TMDB 호출 없이(서버 응답은 page.route로 고정) 실제 브라우저에서
// 채팅 답변 아래 영화 추천 카드(제목·이유·사실 칸·다음 단계·예매 링크·TMDB 출처 표기)가 360px 안에 보이는지 검사한다.
// 고칠 때 주의: recommend_movies 결과 칸 이름(apps/api/app/tools/boxoffice.py recommend_card)을 바꾸면 이 fixture도 바꾼다.
import { expect, test } from "@playwright/test";
import { loginToChat } from "./helpers";
import { fakeChat } from "./width-fixtures";

const NOTICE = "이 서비스는 TMDB API를 사용하지만 TMDB의 보증이나 인증을 받지 않았습니다.";

const CARD = {
  결론: "헝거게임 50번째 추첨의 날 추천",
  기준일: "2026-11-18",
  추천: [
    {
      제목: "헝거게임 50번째 추첨의 날",
      이유: "시리즈를 좋아하신다고 하셔서 이번 주말 친구와 보기 좋아요",
      장르: "액션, SF, 어드벤처",
      "상영 시간": "157분",
      등급: "15세이상관람가",
      감독: "프랜시스 로렌스",
      배우: "조셉 자다, 화이트니 피크",
      "누적 관객": "21,589명",
      평점: "7.4",
    },
    {
      제목: "폭설",
      이유: "조용한 긴장감을 좋아하시면 맞아요",
      장르: "미스터리, 드라마",
      "상영 시간": "88분",
      등급: "15세이상관람가",
      감독: "박선우, 홍의정",
      배우: "김윤석, 구교환",
      "누적 관객": "63,697명",
      평점: "6.0",
    },
  ],
  조건: "친구랑 주말에",
  다음: "보고 싶은 영화의 시간표를 붙여 주시면 회차를 골라 드릴게요.",
  예매: [
    { 극장: "CGV", 주소: "https://www.cgv.co.kr" },
    { 극장: "롯데시네마", 주소: "https://www.lottecinema.co.kr" },
    { 극장: "메가박스", 주소: "https://www.megabox.co.kr" },
  ],
  "출처 표기": NOTICE,
};

test("채팅 답변 아래에서 영화 추천 카드를 360px 안에서 본다", async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 780 });
  await fakeChat(page);
  await page.route("**/api/tasks/*", async (route) => {
    const step = {
      id: 1,
      order: 1,
      tool_name: "recommend_movies",
      label: "영화 추천 정리하는 중",
      status: "done",
      output: { status: "succeeded", data: CARD },
    };
    await route.fulfill({ json: { id: 1, status: "done", steps: [step], approvals: [] } });
  });
  await loginToChat(page);

  const first = page.getByRole("article", { name: "헝거게임 50번째 추첨의 날" }).first();
  await expect(first).toContainText("시리즈를 좋아하신다고 하셔서");
  await expect(first).toContainText("액션, SF, 어드벤처 · 157분 · 15세이상관람가");
  await expect(page.getByRole("article", { name: "폭설" }).first()).toContainText("평점 6.0");
  await expect(page.getByText(CARD.다음).first()).toBeVisible();
  await expect(page.getByRole("link", { name: "메가박스 예매 페이지" }).first()).toHaveAttribute(
    "href",
    "https://www.megabox.co.kr/",
  );
  await expect(page.getByRole("img", { name: "TMDB" }).first()).toBeVisible();
  await expect(page.getByText(NOTICE).first()).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(360);
});
