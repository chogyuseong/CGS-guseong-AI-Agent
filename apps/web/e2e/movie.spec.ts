// 2026-11-18 영화 비서 C 첫 단계. 모델·TMAP 호출 없이, 채팅 답변 아래 단계 카드에 추천 회차·이유·좌석 안내·예매 링크와
// 제외 이유가 실제 브라우저에서 보이는지 검사한다(생활 비서 명세: 백엔드 테스트만으로 사용자 흐름이 끝났다고 하지 않는다).
// 고칠 때 주의: API compare_showtimes 결과 칸 이름(apps/api/app/tools/showtimes.py)을 바꾸면 이 fixture도 같이 바꾼다.
import { expect, test } from "@playwright/test";
import { loginToChat } from "./helpers";
import { fakeChat } from "./width-fixtures";

test("채팅 답변 아래에서 추천 회차와 제외 이유, 극장 예매 링크를 확인한다", async ({ page }) => {
  await fakeChat(page);
  await page.route("**/api/tasks/*", async (route) => {
    const content = {
      결론: "메가박스 코엑스 19:55 회차가 가장 편합니다.",
      이유: "식사 60분, 21:35 종료, 집 도착 22:15 예상으로 조건을 모두 만족합니다.",
      제외: [
        { 회차: "CGV 강남 19:30 회차", 이유: "식사가 35분뿐입니다(원하신 60분보다 25분 부족)." },
      ],
      계산: [
        { 회차: "메가박스 코엑스 19:55 회차", 식사: "60분", 종료: "21:35", "집 도착": "22:15" },
      ],
      좌석: "좌석은 극장에서 확인해 주세요.",
      예매: "https://www.megabox.co.kr",
    };
    const output = {
      status: "succeeded",
      data: { notice: "외부 데이터", content, truncated: false },
      sources: ["https://www.megabox.co.kr"],
      fetched_at: "2026-11-18T00:00:00Z",
    };
    const step = {
      id: 1,
      order: 1,
      tool_name: "compare_showtimes",
      label: "영화 회차 2개 비교하는 중",
      status: "done",
      output,
    };
    await route.fulfill({ json: { id: 1, status: "done", steps: [step], approvals: [] } });
  });
  await loginToChat(page);
  const card = page.getByRole("region", { name: "외부에서 가져온 내용" }).first();
  await expect(card).toContainText("메가박스 코엑스 19:55 회차가 가장 편합니다.");
  await expect(card).toContainText("좌석은 극장에서 확인해 주세요.");
  await expect(card).toContainText("조회 시각");
  await expect(card.getByRole("link", { name: "극장 예매 페이지 열기" })).toHaveAttribute(
    "href",
    "https://www.megabox.co.kr/",
  );
  await card.getByText("계산 자세히 보기").click();
  await expect(card).toContainText("식사가 35분뿐입니다(원하신 60분보다 25분 부족).");
});
