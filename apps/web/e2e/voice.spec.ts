import { expect, type Page, test } from "@playwright/test";
import { loginToChat } from "./helpers";

// 음성 입력 화면 흐름. 실제 마이크·음성 인식은 쓰지 않는다.
// addInitScript는 페이지의 스크립트보다 먼저 실행되어 브라우저의 SpeechRecognition을 가짜로 바꾼다.
const SPOKEN_TEXT = "내일 서울 날씨 알려줘";

// 가짜 인식기: start() 뒤 잠시 후 확정 결과 하나를 주고 끝난다. (실행은 브라우저 안에서 된다)
function installFakeRecognition(spoken: string) {
  class FakeRecognition {
    lang = "";
    continuous = false;
    interimResults = false;
    maxAlternatives = 1;
    onresult: ((event: unknown) => void) | null = null;
    onerror: ((event: unknown) => void) | null = null;
    onend: (() => void) | null = null;
    start() {
      (window as unknown as { __speechLang: string }).__speechLang = this.lang;
      setTimeout(() => {
        const result = Object.assign([{ transcript: spoken, confidence: 0.9 }], { isFinal: true });
        this.onresult?.({ resultIndex: 0, results: [result] });
        this.onend?.();
      }, 300);
    }
    stop() {
      this.onend?.();
    }
    abort() {
      this.onend?.();
    }
  }
  Object.assign(window, {
    SpeechRecognition: FakeRecognition,
    webkitSpeechRecognition: FakeRecognition,
  });
}

// Chrome에는 webkitSpeechRecognition이 실제로 있으므로 둘 다 지워 미지원 브라우저를 흉내 낸다.
function removeRecognition() {
  for (const name of ["SpeechRecognition", "webkitSpeechRecognition"]) {
    Object.defineProperty(window, name, { value: undefined, configurable: true, writable: true });
  }
}

// 메시지 전송(POST)을 세기만 하고 실제 서버로는 보내지 않는다(유료 모델 호출 방지).
async function countMessagePosts(page: Page): Promise<() => number> {
  let posts = 0;
  await page.route("**/api/conversations/*/messages", async (route) => {
    if (route.request().method() === "POST") {
      posts += 1;
      return route.fulfill({ status: 500, json: { detail: "테스트에서는 보내지 않습니다" } });
    }
    return route.fulfill({ status: 200, json: [] });
  });
  return () => posts;
}

test("360px에서 마이크로 말한 문장이 입력창에만 들어가고 전송되지 않는다", async ({ page }) => {
  await page.addInitScript(installFakeRecognition, SPOKEN_TEXT);
  const postCount = await countMessagePosts(page);
  // 서버 HTML(버튼 없음)과 브라우저 첫 화면이 어긋나면 React가 하이드레이션 오류를 콘솔에 남긴다.
  const hydrationErrors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error" && /hydrat/i.test(message.text())) {
      hydrationErrors.push(message.text());
    }
  });
  await loginToChat(page);
  await page.reload(); // 채팅 화면을 서버 HTML부터 다시 받아 하이드레이션을 거치게 한다

  const mic = page.getByRole("button", { name: "음성 입력" });
  const send = page.getByRole("button", { name: "전송" });
  const input = page.getByLabel("메시지 입력");
  await expect(mic).toBeVisible();
  for (const target of [mic, send]) {
    const box = await target.boundingBox();
    expect(box?.width ?? 0).toBeGreaterThanOrEqual(44);
    expect(box?.height ?? 0).toBeGreaterThanOrEqual(44);
  }
  await expect(input).toBeInViewport({ ratio: 1 });
  await expect(send).toBeInViewport({ ratio: 1 });

  // 7단계 보강: 처음 누르면 외부 처리 안내가 먼저 뜨고, 확인해야 듣기 시작한다.
  await mic.click();
  const notice = page.getByRole("region", { name: "음성 입력 안내" });
  await expect(notice).toContainText("Chrome은 Google 서버");
  await expect(notice.getByRole("button", { name: "확인하고 음성 입력" })).toBeInViewport();
  await notice.getByRole("button", { name: "확인하고 음성 입력" }).click();
  await expect(input).toHaveValue(SPOKEN_TEXT);
  await expect(notice).toHaveCount(0);
  await expect(mic).toHaveAttribute("aria-pressed", "false");
  expect(
    await page.evaluate(() => (window as unknown as { __speechLang: string }).__speechLang),
  ).toBe("ko-KR");

  // 화면이 가로로 넘치지 않고, 잠시 기다려도 전송 요청이 나가지 않는다.
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - window.innerWidth,
  );
  expect(overflow).toBeLessThanOrEqual(0);
  await page.waitForTimeout(1000);
  expect(postCount()).toBe(0);
  expect(hydrationErrors).toEqual([]);

  // 한 번 확인하면 이 브라우저에서는 다시 묻지 않는다.
  await input.fill("");
  await mic.click();
  await expect(input).toHaveValue(SPOKEN_TEXT);
  await expect(page.getByRole("region", { name: "음성 입력 안내" })).toHaveCount(0);
});

test("음성 인식을 지원하지 않는 브라우저에서는 마이크 버튼이 없다", async ({ page }) => {
  await page.addInitScript(removeRecognition);
  await loginToChat(page);

  await expect(page.getByRole("button", { name: "전송" })).toBeVisible();
  await expect(page.getByRole("button", { name: "음성 입력" })).toHaveCount(0);
});
