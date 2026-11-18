// 2026-11-18 생활 비서 D 첫 단계. 모델 호출 없이 실제 브라우저에서 확인한다:
// (1) 채팅 답변 아래 공고별 준비 카드(요구 역량 대조·부족한 점·예상 질문·자소서 방향·체크리스트)가 360px 안에 보인다.
// (2) 설정 화면 '내 경험'에서 안내를 읽었다고 체크해야 저장 버튼이 켜진다(서버 응답은 page.route로 고정).
// 고칠 때 주의: show_job_prep 결과 칸 이름(apps/api/app/tools/jobprep.py)을 바꾸면 이 fixture도 바꾼다.
import { expect, test } from "@playwright/test";
import { loginToChat } from "./helpers";
import { fakeChat } from "./width-fixtures";

const CARD = {
  회사: "SK",
  직무: "백엔드 개발자",
  대조: [
    {
      요구: "Python 기반 웹 서비스 개발 경험",
      구분: "필수",
      근거: [
        {
          경험: "마이에이전트",
          인용: "FastAPI와 PostgreSQL로 승인 흐름을 설계했다",
          설명: "Python 웹 서비스 설계 경험",
        },
      ],
    },
    { 요구: "Kubernetes 운영 경험", 구분: "우대", 근거: [] },
  ],
  부족: ["Kubernetes 운영 경험이 없음 — 작은 배포 실습 권장"],
  "예상 질문": ["승인 흐름에서 중복 실행을 어떻게 막았나요?"],
  자소서: ["실행 영수증 설계를 문제 해결 사례로"],
  체크리스트: ["Kubernetes 기초 실습", "프로젝트 아키텍처 1장 정리"],
};

test("채팅 답변 아래에서 공고별 준비 카드 다섯 칸을 360px 안에서 본다", async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 780 });
  await fakeChat(page);
  await page.route("**/api/tasks/*", async (route) => {
    const step = {
      id: 1,
      order: 1,
      tool_name: "show_job_prep",
      label: "SK 지원 준비 카드 만드는 중",
      status: "done",
      output: { status: "succeeded", data: CARD },
    };
    await route.fulfill({ json: { id: 1, status: "done", steps: [step], approvals: [] } });
  });
  await loginToChat(page);

  const table = page.getByRole("region", { name: "요구 역량 대조" }).first();
  await expect(table).toContainText("Python 기반 웹 서비스 개발 경험");
  await expect(table).toContainText("맞는 경험 없음");
  for (const name of ["부족한 점", "예상 면접 질문", "자소서 방향", "준비 체크리스트"]) {
    await expect(page.getByRole("region", { name }).first()).toBeVisible();
  }
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(360);
});

test("설정 '내 경험': 안내를 읽었다고 체크해야 저장할 수 있다", async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 780 });
  await page.route("**/api/experiences", (route) =>
    route.fulfill({
      json: { experiences: [], limit: 10, max_body_chars: 2000, enabled: true },
    }),
  );
  await loginToChat(page);
  await page.getByRole("link", { name: "설정" }).click();

  const region = page.getByRole("region", { name: "내 경험" });
  await region.scrollIntoViewIfNeeded();
  await expect(region).toContainText("암호화해서 저장합니다");
  await region.getByLabel("제목").fill("마이에이전트");
  await region.getByLabel(/본문/).fill("FastAPI로 승인 흐름을 설계했다.");
  const save = region.getByRole("button", { name: "경험 저장" });
  await expect(save).toBeDisabled();
  await region.getByLabel("안내를 읽었습니다").check();
  await expect(save).toBeEnabled();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(360);
});
