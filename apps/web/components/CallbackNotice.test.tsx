import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { CallbackNotice } from "./CallbackNotice";

// Next.js 라우터는 테스트에 없으므로 주소(쿼리)와 replace만 가짜로 둔다.
let query = "";
const replace = vi.fn();
vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(query),
  useRouter: () => ({ replace }),
}));

describe("연결 화면으로 돌아왔을 때의 안내", () => {
  beforeEach(() => {
    replace.mockReset();
  });

  it("연결에 성공했으면 성공 안내를 보여 주고 주소의 쿼리를 지운다", () => {
    query = "google=connected";
    render(<CallbackNotice />);

    expect(screen.getByRole("status")).toHaveTextContent("Google 계정을 연결했습니다");
    expect(replace).toHaveBeenCalledWith("/connections", { scroll: false });
  });

  it("오류 코드별 한국어 안내를 경고로 보여 주고 쿼리를 지운다", () => {
    query = "google=error&reason=scope_missing";
    render(<CallbackNotice />);

    expect(screen.getByRole("alert")).toHaveTextContent("캘린더 권한");
    expect(replace).toHaveBeenCalledWith("/connections", { scroll: false });
  });

  it("쿼리가 없으면 아무것도 보여 주지 않고 주소도 바꾸지 않는다", () => {
    query = "";
    const { container } = render(<CallbackNotice />);

    expect(container).toBeEmptyDOMElement();
    expect(replace).not.toHaveBeenCalled();
  });
});
