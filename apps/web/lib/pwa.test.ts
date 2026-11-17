import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import manifest from "@/app/manifest";
import { APPLE_TOUCH_ICON, THEME_COLORS } from "./pwa";

// 9단계 보강: PWA manifest와 아이콘(BUILD_SPEC 10.1). 경로가 가리키는 파일이 실제로 있고 크기가 맞는지 고정한다.
const PUBLIC = path.resolve(import.meta.dirname, "../public");

function publicFile(src: string): string {
  return path.join(PUBLIC, ...src.replace(/^\//, "").split("/"));
}

// PNG 머리(IHDR)에서 가로·세로를 읽는다. 8바이트 서명 뒤 16~24바이트가 크기다.
function pngSize(file: string): string {
  const bytes = readFileSync(file);
  expect(bytes.subarray(1, 4).toString("ascii")).toBe("PNG");
  return `${bytes.readUInt32BE(16)}x${bytes.readUInt32BE(20)}`;
}

describe("PWA manifest", () => {
  const icons = manifest().icons ?? [];

  it("192·512 아이콘이 있고, 파일이 실제로 있으며 크기가 적힌 값과 같다", () => {
    const sizes = icons.map((icon) => icon.sizes);
    expect(sizes).toContain("192x192");
    expect(sizes).toContain("512x512");
    for (const icon of icons) {
      expect(icon.type).toBe("image/png");
      expect(existsSync(publicFile(icon.src)), icon.src).toBe(true);
      expect(pngSize(publicFile(icon.src))).toBe(icon.sizes);
    }
  });

  it("maskable 아이콘(512)이 따로 있다", () => {
    const maskable = icons.filter((icon) => icon.purpose === "maskable");
    expect(maskable.map((icon) => icon.sizes)).toEqual(["512x512"]);
  });

  it("앱으로 열 때 필요한 값(이름, 시작 주소, standalone, 색)이 있다", () => {
    const value = manifest();
    expect(value.name).toBe("마이에이전트");
    expect(value.start_url).toBe("/chat");
    expect(value.display).toBe("standalone");
    expect(value.theme_color).toBe(THEME_COLORS.light);
    expect(value.background_color).toBe(THEME_COLORS.light);
  });
});

describe("apple-touch-icon과 theme-color", () => {
  it("apple-touch-icon은 180x180 PNG 파일이다", () => {
    expect(pngSize(publicFile(APPLE_TOUCH_ICON))).toBe("180x180");
  });

  it("밝음·어둠 theme-color가 모두 있고 globals.css의 배경색과 같다", () => {
    expect(THEME_COLORS.light).toBe("#ffffff");
    expect(THEME_COLORS.dark).toBe("#0a0a0a");
  });
});
