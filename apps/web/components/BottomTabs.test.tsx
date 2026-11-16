import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { BottomTabs } from "./BottomTabs";

// vi.mock: 모듈을 가짜로 바꾼다. (pytest의 monkeypatch / unittest.mock.patch)
// 테스트에는 Next.js 라우터가 없으므로 "지금 주소는 /chat"이라고 정해 둔다.
let pathname = "/chat";
vi.mock("next/navigation", () => ({
  usePathname: () => pathname,
}));

describe("BottomTabs", () => {
  beforeEach(() => {
    pathname = "/chat";
  });

  it("탭 5개를 명세 순서대로 그린다", () => {
    render(<BottomTabs />);
    const nav = screen.getByRole("navigation", { name: "하단 탭" });
    const links = within(nav).getAllByRole("link");

    expect(links).toHaveLength(5);
    expect(links.map((link) => link.textContent)).toEqual([
      "채팅",
      "작업 기록",
      "자동화",
      "기억",
      "설정",
    ]);
  });

  it("지금 주소의 탭만 현재 페이지로 표시한다", () => {
    render(<BottomTabs />);
    expect(screen.getByRole("link", { name: "채팅" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "설정" })).not.toHaveAttribute("aria-current");
  });

  it("연결 화면(/connections)은 설정 탭 안의 화면으로 표시한다", () => {
    pathname = "/connections";
    render(<BottomTabs />);
    expect(screen.getByRole("link", { name: "설정" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "채팅" })).not.toHaveAttribute("aria-current");
  });
});
