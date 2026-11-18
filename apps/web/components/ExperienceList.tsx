"use client";

// 맡은 일: 설정 화면의 '내 경험' 카드(생활 비서 D 첫 단계). 등록 전 안내 → "안내를 읽었습니다" 체크 → 제목·본문 등록,
//   목록(제목·글자 수), 본문 보기, 지우기.
// 근거: 2026-11-18 사용자 결정(경험 원문은 암호화 + 등록 전 안내, 등록 화면은 설정 안). 채팅으로 받지 않는 이유는
//   원문이 대화 기록에 평문으로 남기 때문(docs/DECISIONS.md "요구사항 밖 기능", 설계 docs/superpowers/specs/2026-11-18-job-prep-design.md).
// 쓰는 곳: app/(tabs)/settings/page.tsx.
// 고칠 때 주의:
// - NOTICE 문장은 docs/DATA_POLICY.md "내 경험" 줄과 같은 내용이어야 한다(저장 위치·모델 전송·보관).
// - 체크는 등록할 때마다 다시 해야 한다(등록 뒤 풀린다). 안내를 한 번 읽고 잊는 것을 막는다.
// - 테스트: components/ExperienceList.test.tsx. e2e는 h2 "내 경험"을 찾는다(settings.spec).
import { BriefcaseBusiness } from "lucide-react";
import { useId, useState } from "react";
import { Button } from "@/components/ui/button";
import {
  type ExperienceList as ExperienceListData,
  type ExperienceSummary,
  useAddExperience,
  useDeleteExperience,
  useExperienceBody,
  useExperiences,
} from "@/lib/experiences";

const NOTICE = [
  "저장 위치: 이 앱의 데이터베이스에 암호화해서 저장합니다(제목은 목록 표시용으로 암호화하지 않습니다).",
  "모델 전송: 채팅에서 채용 공고 준비를 요청할 때만 AI 모델 공급자에게 보냅니다. 외부 검색에는 보내지 않습니다.",
  "전화번호·이메일·주소는 빼고 적어 주세요. 보이면 저장하지 않습니다.",
  "준비 카드와 답변에 인용된 짧은 부분은 대화 기록에 남습니다. 대화를 지우면 함께 지워집니다.",
  "지우기를 누르면 바로 지웁니다. 지운 데이터도 백업에는 최대 30일 동안 남을 수 있습니다.",
];
const TITLE_MAX = 60;

export function ExperienceList() {
  const query = useExperiences();
  return (
    <section
      aria-labelledby="experience-title"
      className="flex min-w-0 shrink-0 flex-col gap-3 rounded-xl border bg-card p-4 text-card-foreground"
    >
      <h2 id="experience-title" className="flex items-center gap-3 text-base font-semibold">
        <BriefcaseBusiness aria-hidden="true" className="size-5 shrink-0" />내 경험
      </h2>
      <p className="text-sm text-muted-foreground">
        자소서·프로젝트 글을 저장해 두면, 채팅에 채용 공고를 붙여 넣고 &quot;이 공고 준비해
        줘&quot;라고 할 때 공고와 맞춰 준비 카드를 만듭니다.
      </p>
      {query.isPending && <p className="text-sm text-muted-foreground">불러오는 중…</p>}
      {query.isError && (
        <p role="alert" className="text-sm text-destructive">
          {query.error.message}
        </p>
      )}
      {query.data && <ExperienceBody data={query.data} />}
    </section>
  );
}

function ExperienceBody({ data }: { data: ExperienceListData }) {
  return (
    <>
      {data.experiences.length > 0 && (
        <ul aria-label="저장한 경험" className="flex flex-col gap-2">
          {data.experiences.map((item) => (
            <ExperienceRow key={item.id} item={item} canOpen={data.enabled} />
          ))}
        </ul>
      )}
      {!data.enabled ? (
        <p className="text-sm text-muted-foreground">
          암호화 키(PAYLOAD_ENCRYPTION_KEY)가 설정되지 않아 경험을 등록할 수 없습니다.
        </p>
      ) : data.experiences.length >= data.limit ? (
        <p className="text-sm text-muted-foreground">
          {data.limit}개까지 저장할 수 있습니다. 하나를 지운 뒤 등록해 주세요.
        </p>
      ) : (
        <ExperienceForm maxBody={data.max_body_chars} />
      )}
    </>
  );
}

function ExperienceRow({ item, canOpen }: { item: ExperienceSummary; canOpen: boolean }) {
  const [open, setOpen] = useState(false);
  const body = useExperienceBody(item.id, open);
  const remove = useDeleteExperience();
  return (
    <li className="flex min-w-0 flex-col gap-2 rounded-lg border p-3">
      <div className="flex min-w-0 items-center gap-2">
        <span className="min-w-0 flex-1 break-words text-sm font-medium">{item.title}</span>
        <span className="shrink-0 text-xs text-muted-foreground">{item.length}자</span>
      </div>
      <div className="flex gap-2">
        {canOpen && (
          <Button variant="outline" className="min-h-11" onClick={() => setOpen((value) => !value)}>
            {open ? "접기" : "보기"}
          </Button>
        )}
        <Button
          variant="outline"
          className="min-h-11"
          disabled={remove.isPending}
          onClick={() => remove.mutate(item.id)}
          aria-label={`${item.title} 지우기`}
        >
          지우기
        </Button>
      </div>
      {open && body.data && (
        <p className="whitespace-pre-wrap break-words text-sm">{body.data.body}</p>
      )}
      {open && body.isError && <p className="text-sm text-destructive">{body.error.message}</p>}
      {remove.isError && (
        <p role="alert" className="text-sm text-destructive">
          {remove.error.message}
        </p>
      )}
    </li>
  );
}

function ExperienceForm({ maxBody }: { maxBody: number }) {
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [agreed, setAgreed] = useState(false);
  const add = useAddExperience();
  const ids = { title: useId(), body: useId(), agree: useId() };
  const ready = agreed && title.trim() !== "" && body.trim() !== "" && !add.isPending;
  const submit = () =>
    add.mutate(
      { title: title.trim(), body: body.trim() },
      {
        onSuccess: () => {
          setTitle("");
          setBody("");
          setAgreed(false);
        },
      },
    );
  return (
    <div className="flex min-w-0 flex-col gap-2 border-t pt-3">
      <p className="text-sm font-medium">등록 전에 확인해 주세요</p>
      <ul className="flex list-disc flex-col gap-1 pl-5 text-sm text-muted-foreground">
        {NOTICE.map((line) => (
          <li key={line} className="break-words">
            {line}
          </li>
        ))}
      </ul>
      <label htmlFor={ids.agree} className="flex min-h-11 items-center gap-2 text-sm">
        <input
          id={ids.agree}
          type="checkbox"
          checked={agreed}
          onChange={(event) => setAgreed(event.target.checked)}
          className="size-5"
        />
        안내를 읽었습니다
      </label>
      <label htmlFor={ids.title} className="text-sm font-medium">
        제목
      </label>
      <input
        id={ids.title}
        value={title}
        maxLength={TITLE_MAX}
        onChange={(event) => setTitle(event.target.value)}
        placeholder="예: 마이에이전트 프로젝트"
        className="min-h-11 w-full rounded-md border bg-background px-3 text-base"
      />
      <label htmlFor={ids.body} className="text-sm font-medium">
        본문 ({body.length}/{maxBody}자)
      </label>
      <textarea
        id={ids.body}
        value={body}
        maxLength={maxBody}
        onChange={(event) => setBody(event.target.value)}
        rows={6}
        className="w-full rounded-md border bg-background px-3 py-2 text-base"
      />
      {add.isError && (
        <p role="alert" className="text-sm text-destructive">
          {add.error.message}
        </p>
      )}
      <Button onClick={submit} disabled={!ready} className="min-h-11">
        경험 저장
      </Button>
    </div>
  );
}
