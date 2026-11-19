import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { Step } from "@/lib/chat";
import { StepTimeline } from "./StepTimeline";

// 영화 비서 C 둘째 단계(2026-11-19): recommend_movies 결과는 단계를 펼친 추천 카드로 보인다.
// 칸 이름은 서버 apps/api/app/tools/boxoffice.py recommend_card와 맞물린다.
function makeStep(overrides: Partial<Step>): Step {
  return {
    id: 1,
    order: 1,
    tool_name: "recommend_movies",
    label: "영화 추천 정리하는 중",
    risk_level: "READ",
    status: "done",
    ok: true,
    input: { picks: [] },
    output: null,
    duration_ms: 5,
    ...overrides,
  };
}

const NOTICE = "이 서비스는 TMDB API를 사용하지만 TMDB의 보증이나 인증을 받지 않았습니다.";

const CARD = {
  결론: "폭설 추천",
  기준일: "2026-11-18",
  추천: [
    {
      제목: "폭설",
      이유: "조용한 긴장감을 좋아하시면 맞아요",
      장르: "미스터리, 드라마",
      "상영 시간": "88분",
      등급: "15세이상관람가",
      감독: "박선우, 홍의정",
      배우: "김윤석, 구교환",
      "누적 관객": "63,697명",
      평점: "6.0",
    },
    {
      제목: "헝거게임 50번째 추첨의 날",
      이유: "시리즈 팬이면 좋아요",
      장르: "액션",
      "상영 시간": "정보 없음",
      등급: "정보 없음",
      감독: "정보 없음",
      배우: "정보 없음",
      "누적 관객": "21,589명",
      평점: "정보 없음",
    },
  ],
  조건: "친구랑 가볍게",
  다음: "보고 싶은 영화의 시간표를 붙여 주시면 회차를 골라 드릴게요.",
  예매: [
    { 극장: "CGV", 주소: "https://www.cgv.co.kr" },
    { 극장: "롯데시네마", 주소: "https://www.lottecinema.co.kr" },
    { 극장: "메가박스", 주소: "javascript:alert(1)" },
  ],
  "출처 표기": NOTICE,
};

function renderCard(card: Record<string, unknown>) {
  render(<StepTimeline steps={[makeStep({ output: { status: "succeeded", data: card } })]} />);
}

describe("MovieRecommendCard", () => {
  it("추천 영화마다 제목·이유·사실 칸을 보인다", () => {
    renderCard(CARD);
    expect(screen.getByText("폭설 추천")).toBeInTheDocument();
    expect(screen.getByText("2026-11-18 박스오피스 기준 · 친구랑 가볍게")).toBeInTheDocument();
    const first = screen.getByRole("article", { name: "폭설" });
    expect(within(first).getByText("조용한 긴장감을 좋아하시면 맞아요")).toBeInTheDocument();
    expect(within(first).getByText("미스터리, 드라마 · 88분 · 15세이상관람가")).toBeInTheDocument();
    expect(within(first).getByText("평점 6.0 · 누적 관객 63,697명")).toBeInTheDocument();
    expect(
      within(first).getByText("감독 박선우, 홍의정 · 배우 김윤석, 구교환"),
    ).toBeInTheDocument();
  });

  it("정보 없음 칸은 그 칸만 빼고 보인다", () => {
    renderCard(CARD);
    const second = screen.getByRole("article", { name: "헝거게임 50번째 추첨의 날" });
    expect(within(second).getByText("액션")).toBeInTheDocument();
    expect(within(second).getByText("누적 관객 21,589명")).toBeInTheDocument();
    expect(within(second).queryByText(/감독/)).not.toBeInTheDocument();
  });

  it("다음 단계 문구와 http 예매 링크만 만든다", () => {
    renderCard(CARD);
    expect(screen.getByText(CARD.다음)).toBeInTheDocument();
    const cgv = screen.getByRole("link", { name: "CGV 예매 페이지" });
    expect(cgv).toHaveAttribute("href", "https://www.cgv.co.kr/"); // URL 정규화(safeHttpUrl)
    expect(cgv).toHaveAttribute("target", "_blank");
    expect(cgv).toHaveAttribute("rel", "noopener noreferrer");
    expect(screen.queryByRole("link", { name: "메가박스 예매 페이지" })).not.toBeInTheDocument();
  });

  it("TMDB를 쓴 결과에만 로고와 출처 문구를 붙인다", () => {
    renderCard(CARD);
    expect(screen.getByText(NOTICE)).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "TMDB" })).toHaveAttribute("src", "/tmdb-logo.svg");
    expect(screen.getByText("영화 정보: KOBIS(영화관입장권통합전산망)")).toBeInTheDocument();
  });

  it("TMDB 없이 만든 카드는 로고를 붙이지 않는다", () => {
    const { "출처 표기": _, ...plain } = CARD;
    renderCard(plain);
    expect(screen.queryByRole("img", { name: "TMDB" })).not.toBeInTheDocument();
    expect(screen.getByText("영화 정보: KOBIS(영화관입장권통합전산망)")).toBeInTheDocument();
  });

  it("입력 JSON은 숨기고 처음부터 펼쳐 둔다", () => {
    renderCard(CARD);
    expect(screen.queryByText("입력")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /영화 추천 정리하는 중/ })).toHaveAttribute(
      "aria-expanded",
      "true",
    );
  });
});
