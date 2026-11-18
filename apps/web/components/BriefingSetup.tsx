"use client";

import { Sunrise } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { type Schedule, useEnableBriefing } from "@/lib/automations";

// 6단계 보강: 아침 브리핑은 사용자가 켠 뒤에만 실행한다(BUILD_SPEC 8.1). 기본 제안 시각은 07:30.
// 끄기·지우기는 아래 예약 목록의 브리핑 항목에서 한다.
//
// 쓰는 곳: AutomationList.tsx의 예약 섹션(브리핑이 바뀔 때마다 key로 새로 그린다).
// 서버 호출: lib/automations.ts의 useEnableBriefing → PUT /api/schedules/briefing {time_of_day: "HH:MM"}.
//   한 요청으로 만들기·시각 바꾸기·다시 켜기를 한다(사용자당 하나, DECISIONS 2026-11-17 6단계 보강).
//   성공·실패 모두 예약 목록을 다시 가져온다.
// 근거: BUILD_SPEC 8.1(ScheduledJob), DECISIONS 2026-11-17 "아침 브리핑은 사용자가 자동화 화면에서 켤 때만 생긴다".
//
// 고칠 때 주의:
// - DEFAULT_TIME은 서버 rules.py의 BRIEFING_TIME(07:30)과 같은 값을 글자로 둔 것이다. 바꾸면 둘 다 고친다.
// - 서버는 "HH:MM"(24시간)만 받는다(rules.parse_time_of_day). <input type="time">은 step을 주지 않으면 이 모양을 준다.
// - 테스트: components/BriefingSetup.test.tsx.
const DEFAULT_TIME = "07:30";

// briefing: 예약 목록에서 is_briefing인 항목(없으면 undefined = 아직 켠 적 없음 또는 지움).
// maxActive: 409 limit_reached 오류 문구에 넣을 활성 예약 한도.
type Props = { briefing: Schedule | undefined; maxActive: number };

// 브리핑 켜기·시각 바꾸기 카드. 누르기 전에는 서버에 아무것도 만들지 않는다(동의 없이 비용이 드는 예약을 만들지 않게).
// 연타 방지는 mutation의 isPending으로 버튼을 막는 것뿐이다. PUT은 같은 시각이면 결과가 같아 두 번 가도 해가 없다.
export function BriefingSetup({ briefing, maxActive }: Props) {
  const active = briefing?.is_active === true;
  const [time, setTime] = useState(briefing?.time_of_day ?? DEFAULT_TIME);
  const [error, setError] = useState<string | null>(null);
  const enable = useEnableBriefing(maxActive);

  function submit() {
    setError(null);
    enable.mutate(time, { onError: (caught) => setError(caught.message) });
  }

  return (
    <section
      aria-labelledby="briefing-title"
      className="flex min-w-0 flex-col gap-2 rounded-xl border bg-card p-4 text-card-foreground"
    >
      <h3 id="briefing-title" className="flex items-center gap-2 text-base font-semibold">
        <Sunrise aria-hidden="true" className="size-5 shrink-0" />
        아침 브리핑 설정
      </h3>
      <p className="text-sm text-muted-foreground">
        {active
          ? `매일 ${briefing?.time_of_day}에 실행 중`
          : "매일 정한 시각에 오늘 일정·날씨·할 일을 요약해 드립니다. 켜기 전에는 실행하지 않습니다."}
      </p>
      <p className="text-sm text-muted-foreground">켜 두면 매일 한 번 모델 비용이 듭니다.</p>
      <div className="flex min-w-0 items-center gap-2">
        <label htmlFor="briefing-time" className="shrink-0 text-sm">
          브리핑 시각
        </label>
        <input
          id="briefing-time"
          type="time"
          value={time}
          onChange={(event) => setTime(event.target.value)}
          className="h-11 min-w-0 flex-1 rounded-md border bg-background px-3 text-base"
        />
      </div>
      <Button
        type="button"
        onClick={submit}
        disabled={enable.isPending || time === ""}
        className="h-11 w-full text-base"
      >
        {active ? "시각 바꾸기" : "브리핑 켜기"}
      </Button>
      {error && (
        <p role="alert" className="break-words text-sm text-destructive">
          {error}
        </p>
      )}
    </section>
  );
}
