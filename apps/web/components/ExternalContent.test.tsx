import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ExternalContent } from "./ExternalContent";

// 2026-11-18: 서버 sources에 같은 주소가 두 번 오면 React key가 겹쳤다(출처 목록 표시가 꼬일 수 있음).
describe("ExternalContent 출처 목록", () => {
  it("같은 출처가 두 번 와도 key가 겹치지 않는다", () => {
    const errors = vi.spyOn(console, "error").mockImplementation(() => {});
    const url = "https://example.com/a";
    render(
      <ExternalContent
        external={{ content: "본문", truncated: false, sources: [url, url], fetchedAt: null }}
      />,
    );

    const list = screen.getByText("출처").nextElementSibling as HTMLElement;
    expect(within(list).getAllByRole("listitem")).toHaveLength(2);
    expect(errors.mock.calls.flat().join(" ")).not.toMatch(/same key/);
    errors.mockRestore();
  });
});
