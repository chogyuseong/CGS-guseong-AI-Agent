import { Database } from "lucide-react";

// 5단계 보강: 무엇을 얼마나 두고, 지우면 어디까지 지워지는지 설정 화면에서 알린다(BUILD_SPEC 5.5, docs/DATA_POLICY.md).
// "삭제 = 모든 곳에서 즉시 완전 삭제"라고 쓰지 않는다. 기간은 서버 기본값(EXECUTION_RETENTION_DAYS 7, RECEIPT_RETENTION_DAYS 90).
const LINES = [
  "작업 실행 기록은 끝난 뒤 7일이 지나면 지웁니다(모델 기록, 단계 입력·출력). 대화 메시지와 단계 이름·결과 상태는 남습니다.",
  "외부 서비스에서 한 일의 실행 영수증은 90일 뒤 지웁니다.",
  "대화를 지우면 메시지와 실행 기록을 바로 지웁니다. 대화를 지워도 그 대화에서 만든 기억은 남습니다.",
  "기억을 지워도 예전 대화에 같은 내용이 남아 있으면 그 대화를 이어갈 때 참고될 수 있습니다.",
  "지운 데이터도 백업에는 최대 30일 동안 남을 수 있습니다.",
];

export function DataRetentionNotice() {
  return (
    <section
      aria-labelledby="data-retention-title"
      className="flex min-w-0 flex-col gap-2 rounded-xl border bg-card p-4 text-card-foreground"
    >
      <h2 id="data-retention-title" className="flex items-center gap-3 text-base font-semibold">
        <Database aria-hidden="true" className="size-5 shrink-0" />
        데이터 보관
      </h2>
      <ul className="flex list-disc flex-col gap-1 pl-5 text-sm text-muted-foreground">
        {LINES.map((line) => (
          <li key={line} className="break-words">
            {line}
          </li>
        ))}
      </ul>
    </section>
  );
}
