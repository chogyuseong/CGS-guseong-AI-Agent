import path from "node:path";
import { expect, type Page } from "@playwright/test";

// 명도 대비 점검(9단계 보강, BUILD_SPEC 10.1 "명도 대비"). axe 같은 도구를 새로 들이지 않고 WCAG 2.x 식으로 직접 잰다.
// - 대상: 화면에 보이는 글자가 직접 들어 있는 요소(비활성 버튼·입력칸은 WCAG 예외라 뺀다)
// - 글자색: color에 요소와 조상의 opacity를 곱한 값 / 배경: 조상의 background-color를 아래부터 겹친 값
// - 기준: 보통 글자 4.5:1, 큰 글자(24px 이상, 또는 18.66px 이상 굵은 글자) 3:1
// 색 문자열(oklch 등)은 캔버스에 칠해 sRGB 값으로 읽는다(브라우저가 직접 바꾸므로 따로 변환식이 필요 없다).
// 배경 그림·그라데이션·겹친 형제 요소는 보지 않는다(이 앱 화면에는 없다).
type Rgba = [number, number, number, number];
type Helpers = {
  ratio: (el: Element, style: CSSStyleDeclaration) => { value: number; background: Rgba };
};
type ContrastWindow = Window & { __contrast: Helpers };

function installHelpers(): void {
  const context = document.createElement("canvas").getContext("2d", { willReadFrequently: true });
  const toRgba = (css: string): Rgba => {
    if (!context) return [0, 0, 0, 1];
    context.clearRect(0, 0, 1, 1);
    context.fillStyle = css;
    context.fillRect(0, 0, 1, 1);
    const [r = 0, g = 0, b = 0, a = 255] = context.getImageData(0, 0, 1, 1).data;
    return [r, g, b, a / 255];
  };
  const over = ([r, g, b, a]: Rgba, [br, bg, bb, ba]: Rgba): Rgba => {
    const alpha = a + ba * (1 - a);
    const mix = (top: number, bottom: number) => (top * a + bottom * ba * (1 - a)) / (alpha || 1);
    return [mix(r, br), mix(g, bg), mix(b, bb), alpha];
  };
  const lin = (v: number) =>
    v / 255 <= 0.03928 ? v / 255 / 12.92 : ((v / 255 + 0.055) / 1.055) ** 2.4;
  const luminance = ([r, g, b]: Rgba) => 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
  const ancestors = (el: Element) => {
    const list: Element[] = [];
    for (let node: Element | null = el; node; node = node.parentElement) list.push(node);
    return list;
  };
  const ratio = (el: Element, style: CSSStyleDeclaration) => {
    const chain = ancestors(el);
    const white: Rgba = [255, 255, 255, 1];
    const background = chain
      .map((node) => toRgba(getComputedStyle(node).backgroundColor))
      .reduceRight((bottom, top) => over(top, bottom), white);
    const opacity = chain.reduce(
      (value, node) => value * Number(getComputedStyle(node).opacity),
      1,
    );
    const [r, g, b, a] = toRgba(style.color);
    const text = over([r, g, b, a * opacity], background);
    const [one, two] = [luminance(text), luminance(background)];
    return { value: (Math.max(one, two) + 0.05) / (Math.min(one, two) + 0.05), background };
  };
  (window as unknown as ContrastWindow).__contrast = { ratio };
}

// 브라우저 안에서 실행된다. 기준에 못 미치는 글자를 한국어 문장 목록으로 돌려준다.
function contrastIssues(): string[] {
  const { ratio } = (window as unknown as ContrastWindow).__contrast;
  const hasOwnText = (el: Element) =>
    [...el.childNodes].some((n) => n.nodeType === Node.TEXT_NODE && n.textContent?.trim());
  const issues: string[] = [];
  for (const el of document.body.querySelectorAll("*")) {
    if (!hasOwnText(el) || el.closest(":disabled, [aria-disabled='true']")) continue;
    const style = getComputedStyle(el);
    const box = el.getBoundingClientRect();
    if (box.width < 1 || box.height < 1 || style.visibility === "hidden") continue;
    const { value, background } = ratio(el, style);
    const size = Number.parseFloat(style.fontSize);
    const large = size >= 24 || (size >= 18.66 && Number(style.fontWeight) >= 700);
    if (value >= (large ? 3 : 4.5)) continue;
    const name = `${el.tagName.toLowerCase()}「${(el.textContent ?? "").trim().slice(0, 20)}」`;
    const bg = background.slice(0, 3).map(Math.round).join(",");
    issues.push(`${name} 대비 ${value.toFixed(2)} (글자 ${style.color} / 배경 rgb(${bg}))`);
  }
  return issues;
}

// 지금 화면의 대비를 검사하고, CONTRAST_SHOTS_DIR(저장소 밖 폴더)이 있으면 스크린샷을 남긴다.
export async function auditContrast(page: Page, label: string): Promise<void> {
  // 시트·대화상자가 열리는 중(opacity 전환)이면 글자가 반투명이라 잘못 잰다. 애니메이션이 끝난 뒤 잰다.
  // 끝없이 도는 애니메이션(진행 중 아이콘)은 기다리지 않는다.
  await page.evaluate(() =>
    Promise.all(
      document
        .getAnimations()
        .filter((a) => a.effect?.getTiming().iterations !== Number.POSITIVE_INFINITY)
        .map((a) => a.finished),
    ),
  );
  await page.evaluate(installHelpers);
  const issues = await page.evaluate(contrastIssues);
  const dir = process.env.CONTRAST_SHOTS_DIR;
  if (dir) await page.screenshot({ path: path.join(dir, `${label}.png`) });
  expect(issues, `${label} 화면의 대비가 부족한 글자`).toEqual([]);
}
