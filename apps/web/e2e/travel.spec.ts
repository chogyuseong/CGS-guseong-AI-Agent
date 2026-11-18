// 2026-11-18 이동 비서 B 첫 단계. 모델·TMAP·카카오 호출 없이, 채팅 답변 아래 단계 카드에 출발 계산 결론·이유·조회 시각과
// 지도 링크가 실제 브라우저에서 보이는지 검사한다(생활 비서 명세 인수 조건 8: 백엔드 테스트만으로 끝내지 않는다).
// 고칠 때 주의: API plan_departure 결과 칸 이름(apps/api/app/tools/tmap.py)을 바꾸면 이 fixture도 같이 바꾼다.
import { expect, test } from "@playwright/test";
import { loginToChat } from "./helpers";
import { fakeChat } from "./width-fixtures";

const MAP = "https://map.kakao.com/link/by/traffic/역삼역 2호선,37.5006,127.0364/잠실역 2호선,37.5130,127.1000";

test("채팅 답변 아래에서 권장 출발 시각과 앞 일정 뒤 여유, 지도 링크를 확인한다", async ({ page }) => {
  await fakeChat(page);
  await page.route("**/api/tasks/*", async (route) => {
    const content = {
      결론: "17:50에 출발하면 18:45에 도착합니다(19:00보다 15분 일찍).",
      이유: "앞 일정이 끝나고 10분 여유가 있습니다.",
      "다른 경로": "6분 늦지만 환승이 1번 적습니다.",
      경로: [{ 경로: "도보 → 수도권2호선 → 도보", 소요: "55분", 환승: "2회", 도보: "10분", 요금: "1,550원" }],
      지도: MAP,
    };
    const output = {
      status: "succeeded",
      data: { notice: "외부 데이터", content, truncated: false },
      sources: [MAP],
      fetched_at: "2026-11-18T00:00:00Z",
    };
    const step = {
      id: 1,
      order: 1,
      tool_name: "plan_departure",
      label: "'잠실역'까지 출발 시각 계산하는 중",
      status: "done",
      output,
    };
    await route.fulfill({ json: { id: 1, status: "done", steps: [step], approvals: [] } });
  });
  await loginToChat(page);
  const card = page.getByRole("region", { name: "외부에서 가져온 내용" }).first();
  await expect(card).toContainText("17:50에 출발하면 18:45에 도착합니다");
  await expect(card).toContainText("앞 일정이 끝나고 10분 여유가 있습니다.");
  await expect(card).toContainText("조회 시각");
  await expect(card.getByRole("link", { name: "지도에서 경로 열기" })).toHaveAttribute("href", encodeURI(MAP));
  await card.getByText("경로 자세히 보기").click();
  await expect(card).toContainText("6분 늦지만 환승이 1번 적습니다.");
});
