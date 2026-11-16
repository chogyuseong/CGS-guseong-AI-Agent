import path from "node:path";
import { expect, type Page } from "@playwright/test";

// 폭 점검의 검사 함수. 브라우저 안에서 지금 보이는 화면을 훑어 문제를 한국어 문장 목록으로 돌려준다.
// - 가로 스크롤(문서 폭 > 화면 폭)
// - 화면 밖으로 나간 요소(스크롤 상자 안에서 잘려 보이지 않는 부분은 뺀다)
// - 상자보다 넓은 글자(넘친 글자)
// - 모바일 폭에서 높이 44px 미만인 버튼·입력칸(문장 안의 글자 링크는 뺀다)
// - 서로 겹친 버튼·링크(스크롤 상자 밖으로 가려진 부분은 뺀다. 시트가 열려 있으면 시트 안만 본다)
type Box = { left: number; right: number; top: number; bottom: number };
type Helpers = { name: (el: Element) => string; shown: (el: Element) => Box | null };
type AuditWindow = Window & { __widthAudit: Helpers };

// 아래 검사들이 함께 쓰는 도우미를 브라우저의 window에 둔다(page.evaluate 함수는 바깥 변수를 못 쓴다).
function installHelpers(): void {
  const name = (el: Element) =>
    `${el.tagName.toLowerCase()}「${(el.textContent ?? "").trim().slice(0, 24)}」`;
  // 스크롤·잘림 상자(overflow가 visible이 아닌 조상)와 겹치는 부분만 남긴 실제로 보이는 영역.
  const shown = (el: Element): Box | null => {
    const r = el.getBoundingClientRect();
    if (r.width <= 1 || r.height <= 1 || getComputedStyle(el).visibility === "hidden") return null;
    const box = { left: r.left, right: r.right, top: r.top, bottom: r.bottom };
    for (let parent = el.parentElement; parent; parent = parent.parentElement) {
      const style = getComputedStyle(parent);
      if (style.overflowX === "visible" && style.overflowY === "visible") continue;
      const p = parent.getBoundingClientRect();
      box.left = Math.max(box.left, p.left);
      box.right = Math.min(box.right, p.right);
      box.top = Math.max(box.top, p.top);
      box.bottom = Math.min(box.bottom, p.bottom);
    }
    return box.right - box.left > 1 && box.bottom - box.top > 1 ? box : null;
  };
  (window as unknown as AuditWindow).__widthAudit = { name, shown };
}

// 가로 스크롤, 화면 밖 요소, 넘친 글자.
function layoutIssues(): string[] {
  const { name, shown } = (window as unknown as AuditWindow).__widthAudit;
  const issues: string[] = [];
  const width = document.documentElement.clientWidth;
  if (document.documentElement.scrollWidth > width) {
    issues.push(`가로 스크롤: 문서 폭 ${document.documentElement.scrollWidth} > ${width}`);
  }
  for (const el of document.body.querySelectorAll("*")) {
    const box = shown(el);
    if (!box) continue;
    if (box.left < -0.5 || box.right > width + 0.5) {
      issues.push(`화면 밖: ${name(el)} ${Math.round(box.left)}~${Math.round(box.right)}`);
    }
    const style = getComputedStyle(el);
    const block = style.display !== "inline" && style.overflowX === "visible";
    if (block && el.children.length === 0 && el.scrollWidth > el.clientWidth + 1) {
      issues.push(`넘친 글자: ${name(el)} ${el.scrollWidth} > ${el.clientWidth}`);
    }
  }
  return issues;
}

// 누르는 것들: 모바일 폭의 44px 미만, 서로 겹침. 시트(dialog)가 열려 있으면 그 안만 본다.
function targetIssues(mobile: boolean): string[] {
  const { name, shown } = (window as unknown as AuditWindow).__widthAudit;
  const issues: string[] = [];
  const root = document.querySelector('[role="dialog"]') ?? document.body;
  const targets = [
    ...root.querySelectorAll("button, a, [role=switch], summary, input, textarea, select"),
  ]
    .filter((el) => getComputedStyle(el).display !== "inline")
    .map((el) => ({ el, box: shown(el) }))
    .filter((item): item is { el: Element; box: Box } => item.box !== null);
  for (const { el } of mobile ? targets : []) {
    const height = el.getBoundingClientRect().height;
    if (height < 43.5) issues.push(`작은 버튼: ${name(el)} 높이 ${Math.round(height)}`);
  }
  for (const [index, a] of targets.entries()) {
    for (const b of targets.slice(index + 1)) {
      if (a.el.contains(b.el) || b.el.contains(a.el)) continue;
      const x = Math.min(a.box.right, b.box.right) - Math.max(a.box.left, b.box.left);
      const y = Math.min(a.box.bottom, b.box.bottom) - Math.max(a.box.top, b.box.top);
      if (x > 2 && y > 2) issues.push(`겹침: ${name(a.el)} / ${name(b.el)}`);
    }
  }
  return issues;
}
// 넓은 화면(768px 초과)에서 본문(main의 자식들)과 하단 탭이 읽기 좋은 폭으로 가운데에 있는지.
function wideInPage(): string[] {
  const issues: string[] = [];
  const width = document.documentElement.clientWidth;
  const blocks = document.querySelectorAll('main > *:not(.sr-only), nav[aria-label="하단 탭"] ul');
  for (const el of blocks) {
    const rect = el.getBoundingClientRect();
    if (rect.width <= 1) continue;
    const label = `${el.tagName.toLowerCase()}「${(el.textContent ?? "").trim().slice(0, 16)}」`;
    if (rect.width > 800) issues.push(`너무 넓음: ${label} 폭 ${Math.round(rect.width)}`);
    if (Math.abs(rect.left - (width - rect.right)) > 2) issues.push(`한쪽으로 치우침: ${label}`);
  }
  return issues;
}

// 세로로 스크롤되는 본문을 맨 아래까지 내린 화면도 찍고 원래 자리로 돌린다(스크린샷 확인용).
async function shootScrolled(page: Page, file: string) {
  const moved = await page.evaluate(() => {
    const scrollers = [...document.querySelectorAll("main *")].filter(
      (el) => el.scrollHeight > el.clientHeight + 1 && getComputedStyle(el).overflowY === "auto",
    );
    for (const el of scrollers) {
      el.setAttribute("data-shot-top", String(el.scrollTop));
      el.scrollTop = el.scrollHeight;
    }
    return scrollers.length;
  });
  if (moved === 0) return;
  await page.screenshot({ path: file });
  await page.evaluate(() => {
    for (const el of document.querySelectorAll("[data-shot-top]")) {
      el.scrollTop = Number(el.getAttribute("data-shot-top"));
      el.removeAttribute("data-shot-top");
    }
  });
}

const SHOT_DIR = process.env.WIDTH_SHOTS_DIR;

// 화면 하나를 점검한다. WIDTH_SHOTS_DIR가 있으면 그 폴더에 스크린샷도 남긴다(저장소 밖 폴더를 쓴다).
export async function auditScreen(page: Page, screen: string, width: number): Promise<void> {
  // 시트가 미끄러져 들어오는 등 움직이는 중인 화면을 재지 않도록 애니메이션이 끝나길 기다린다.
  // (계속 도는 로딩 아이콘처럼 끝이 없는 애니메이션은 기다리지 않는다)
  await page.waitForFunction(() =>
    document
      .getAnimations()
      .every(
        (animation) =>
          animation.playState !== "running" ||
          animation.effect?.getTiming().iterations === Number.POSITIVE_INFINITY,
      ),
  );
  if (SHOT_DIR) {
    await page.screenshot({ path: path.join(SHOT_DIR, `${width}-${screen}.png`) });
    await shootScrolled(page, path.join(SHOT_DIR, `${width}-${screen}-end.png`));
  }
  await page.evaluate(installHelpers);
  const issues = await page.evaluate(layoutIssues);
  issues.push(...(await page.evaluate(targetIssues, width < 768)));
  if (width > 768) issues.push(...(await page.evaluate(wideInPage)));
  // soft: 한 화면에서 문제가 나와도 나머지 화면까지 점검하고 끝에 모아서 실패시킨다.
  expect.soft(issues, `${width}px ${screen}`).toEqual([]);
}
