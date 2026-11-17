"use client";

import { Sunrise } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { type Schedule, useEnableBriefing } from "@/lib/automations";

// 6단계 보강: 아침 브리핑은 사용자가 켠 뒤에만 실행한다(BUILD_SPEC 8.1). 기본 제안 시각은 07:30.
// 끄기·지우기는 아래 예약 목록의 브리핑 항목에서 한다.
const DEFAULT_TIME = "07:30";

type Props = { briefing: Schedule | undefined; maxActive: number };

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
