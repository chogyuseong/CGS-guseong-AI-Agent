import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

// 9단계 보강: 다크 모드(BUILD_SPEC 10.1). 시스템 설정(prefers-color-scheme)을 따르고,
// 화면 코드는 globals.css의 색 토큰만 쓴다(밝음·어둠 값이 토큰 한 곳에서 바뀌게).
const WEB = path.resolve(import.meta.dirname, "..");
const css = readFileSync(path.join(WEB, "app/globals.css"), "utf8");

function sourceFiles(dir: string, found: string[] = []): string[] {
  for (const entry of readdirSync(path.join(WEB, dir), { withFileTypes: true })) {
    const relative = `${dir}/${entry.name}`;
    // shadcn이 만든 components/ui는 토큰과 dark: 변형만 쓴다. lib/api는 생성 파일.
    if (relative === "components/ui" || relative === "lib/api") continue;
    if (entry.isDirectory()) sourceFiles(relative, found);
    else if (/\.tsx?$/.test(entry.name) && !/\.test\.tsx?$/.test(entry.name)) found.push(relative);
  }
  return found;
}

// Tailwind 기본 팔레트 색(bg-white, text-green-600, border-amber-500/40 …)과 hex 색.
const PALETTE =
  /\b(?:bg|text|border|ring|fill|stroke|outline|divide|from|to|via)-(?:white|black|(?:slate|gray|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose)-\d{2,3})\b/;
const HEX = /#[0-9a-fA-F]{3,8}\b/;

describe("다크 모드 토큰", () => {
  it("어두운 값은 시스템 설정(prefers-color-scheme: dark)을 따른다", () => {
    expect(css).toMatch(/@media \(prefers-color-scheme: dark\)\s*\{\s*:root\s*\{/);
    expect(css).toContain("@custom-variant dark (@media (prefers-color-scheme: dark));");
    expect(css).not.toMatch(/^\.dark\s*\{/m);
  });

  it("브라우저 기본 요소(스크롤바, 입력칸)도 어둡게 그리도록 color-scheme을 알린다", () => {
    expect(css).toMatch(/color-scheme:\s*light dark;/);
  });

  it("성공·주의 상태 색 토큰이 밝음·어둠 모두에 있다", () => {
    for (const token of ["--success:", "--warning:"]) {
      expect(css.split(token).length - 1, token).toBe(2);
    }
    expect(css).toContain("--color-success: var(--success);");
    expect(css).toContain("--color-warning: var(--warning);");
  });
});

describe("화면 코드에 고정 색이 없다", () => {
  const files = sourceFiles("app").concat(sourceFiles("components"));

  it.each(files)("%s", (file) => {
    const lines = readFileSync(path.join(WEB, file), "utf8").split("\n");
    const found = lines
      .map((line, index) => ({ line, at: index + 1 }))
      .filter(({ line }) => PALETTE.test(line) || HEX.test(line))
      .map(({ line, at }) => `${at}: ${line.trim()}`);
    expect(found).toEqual([]);
  });
});
