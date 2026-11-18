// 맡은 일: 채팅 단계의 공고별 준비 카드(show_job_prep 결과)와 내 경험 읽기(list_experiences) 표시. 생활 비서 D 첫 단계.
// 서버가 요구 사항 인용(붙여 넣은 공고)과 근거 인용(저장한 경험)을 글자로 확인한 뒤에만 이 카드가 생긴다
//   (apps/api/app/tools/jobprep.py). 화면은 받은 글자를 텍스트로만 그린다(HTML로 해석하지 않음).
// 쓰는 곳: components/StepTimeline.tsx.
// 고칠 때 주의: 칸 이름(회사·직무·대조·요구·구분·근거·경험·인용·설명·부족·예상 질문·자소서·체크리스트)은 서버
//   jobprep.py와 글자로 맞물린다. list_experiences 저장 모양 {stored: false, experience_count}는 서버
//   results.output_for_storage가 정한다. 테스트: components/JobPrepCard.test.tsx.
import { withUniqueKeys } from "@/lib/uniqueKeys";

type Row = { 요구: string; 구분: string; 근거: { 경험: string; 인용: string; 설명: string }[] };

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function strings(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string")
    : [];
}

function rows(value: unknown): Row[] {
  if (!Array.isArray(value)) return [];
  return value.filter(isRecord).map((row) => ({
    요구: String(row.요구 ?? ""),
    구분: String(row.구분 ?? ""),
    근거: Array.isArray(row.근거)
      ? row.근거.filter(isRecord).map((cite) => ({
          경험: String(cite.경험 ?? ""),
          인용: String(cite.인용 ?? ""),
          설명: String(cite.설명 ?? ""),
        }))
      : [],
  }));
}

// 성공한 show_job_prep 출력({status, data: 카드})에서 카드, 아니면 null.
export function readJobPrep(output: unknown): Record<string, unknown> | null {
  if (!isRecord(output) || output.status !== "succeeded" || !isRecord(output.data)) return null;
  return "대조" in output.data ? output.data : null;
}

// list_experiences의 저장 모양에서 읽은 경험 수, 아니면 null.
export function storedExperienceCount(output: unknown): number | null {
  const data = isRecord(output) ? output.data : null;
  const content = isRecord(data) ? data.content : null;
  if (!isRecord(content) || content.stored !== false) return null;
  return typeof content.experience_count === "number" ? content.experience_count : null;
}

export function JobPrepCard({ card }: { card: Record<string, unknown> }) {
  return (
    <div className="flex min-w-0 flex-col gap-3 rounded-lg border p-3 text-sm">
      <p className="break-words font-semibold">
        {String(card.회사 ?? "")} · {String(card.직무 ?? "")} 지원 준비
      </p>
      <section aria-label="요구 역량 대조" className="flex flex-col gap-2">
        <p className="font-medium">요구 역량 대조</p>
        <ul className="flex flex-col gap-2">
          {withUniqueKeys(rows(card.대조), (row) => `${row.구분}-${row.요구}`).map(
            ({ item, key }) => (
              <RequirementRow key={key} row={item} />
            ),
          )}
        </ul>
      </section>
      <BulletSection title="부족한 점" items={strings(card.부족)} />
      <BulletSection title="예상 면접 질문" items={strings(card["예상 질문"])} />
      <BulletSection title="자소서 방향" items={strings(card.자소서)} />
      <BulletSection title="준비 체크리스트" items={strings(card.체크리스트)} />
    </div>
  );
}

function RequirementRow({ row }: { row: Row }) {
  return (
    <li className="flex min-w-0 flex-col gap-1 rounded-md bg-muted p-2">
      <p className="break-words">
        <span className="mr-1 text-xs text-muted-foreground">[{row.구분}]</span>
        {row.요구}
      </p>
      {row.근거.length === 0 ? (
        <p className="text-xs text-muted-foreground">맞는 경험 없음</p>
      ) : (
        withUniqueKeys(row.근거, (cite) => `${cite.경험}-${cite.인용}`).map(({ item, key }) => (
          <p key={key} className="break-words text-xs">
            <span className="font-medium">{item.경험}</span>: 「{item.인용}」 — {item.설명}
          </p>
        ))
      )}
    </li>
  );
}

function BulletSection({ title, items }: { title: string; items: string[] }) {
  if (items.length === 0) return null;
  return (
    <section aria-label={title} className="flex flex-col gap-1">
      <p className="font-medium">{title}</p>
      <ul className="flex list-disc flex-col gap-1 pl-5">
        {withUniqueKeys(items, (item) => item).map(({ item, key }) => (
          <li key={key} className="break-words">
            {item}
          </li>
        ))}
      </ul>
    </section>
  );
}
