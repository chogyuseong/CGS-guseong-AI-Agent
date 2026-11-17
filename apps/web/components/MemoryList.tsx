"use client";

import { LoaderCircle } from "lucide-react";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { Button, buttonVariants } from "@/components/ui/button";
import {
  conversationHref,
  formatMemoryDate,
  formatMessageTime,
  type Memory,
  memoryCountText,
  useDeleteAllMemories,
  useDeleteMemory,
  useMemories,
} from "@/lib/memories";
import { cn } from "@/lib/utils";

// 기억 탭(5단계): 목록, 원래 메시지 보기, 한 건 삭제, 전체 삭제(화면 안 확인 한 번).
// 기억 내용과 원래 메시지는 남이 아니라 사용자가 한 말이지만, 그래도 React 텍스트로만 그린다(HTML로 해석하지 않음).

type Feedback = { tone: "success" | "error"; text: string };

const ONE_DELETED = "기억을 지웠습니다. 다음 대화부터 에이전트가 이 기억을 쓰지 않습니다.";
const FALLBACK_ERROR = "요청을 처리하지 못했습니다. 잠시 후 다시 시도해 주세요.";
const BIG_BUTTON = "h-12 w-full min-w-0 text-base";
const ITEM_BUTTON = "h-11 min-w-0 text-sm";

function errorText(caught: unknown): string {
  return caught instanceof Error ? caught.message : FALLBACK_ERROR;
}

// 누르는 즉시 ref로 잠가 연타해도 한 번만 실행한다(state만으로는 같은 틱의 두 번째 클릭을 못 막는다).
function useLocked() {
  const [busy, setBusy] = useState(false);
  const locked = useRef(false);
  async function run(action: () => Promise<void>) {
    if (locked.current) return;
    locked.current = true;
    setBusy(true);
    try {
      await action();
    } finally {
      locked.current = false;
      setBusy(false);
    }
  }
  return { busy, run };
}

type DeleteAllFlow = ReturnType<typeof useDeleteAllFlow>;

function useDeleteAllFlow(deleteAll: () => Promise<number>, report: (f: Feedback | null) => void) {
  const [confirming, setConfirming] = useState(false);
  const { busy, run } = useLocked();
  const submit = () =>
    run(async () => {
      report(null);
      try {
        const deleted = await deleteAll();
        setConfirming(false);
        report({ tone: "success", text: `기억 ${deleted}개를 모두 지웠습니다.` });
      } catch (caught) {
        report({ tone: "error", text: errorText(caught) });
      }
    });
  const ask = () => {
    report(null);
    setConfirming(true);
  };
  return { confirming, busy, submit, ask, cancel: () => setConfirming(false) };
}

export function MemoryList() {
  const deleteOne = useDeleteMemory();
  const deleteAll = useDeleteAllMemories();
  const [feedback, setFeedback] = useState<Feedback | null>(null);
  const allFlow = useDeleteAllFlow(() => deleteAll.mutateAsync(), setFeedback);

  async function removeOne(memoryId: number) {
    setFeedback(null);
    try {
      await deleteOne.mutateAsync(memoryId);
      setFeedback({ tone: "success", text: ONE_DELETED });
    } catch (caught) {
      setFeedback({ tone: "error", text: errorText(caught) });
    }
  }

  return (
    <div className="flex min-w-0 flex-col gap-3">
      <FeedbackLine feedback={feedback} />
      <MemoryBody allFlow={allFlow} onDelete={removeOne} />
    </div>
  );
}

// 오류는 긴 목록 아래쪽에서 눌렀어도 보이도록 안내 줄까지 스크롤한다(성공은 자리를 옮기지 않는다).
function FeedbackLine({ feedback }: { feedback: Feedback | null }) {
  const ref = useRef<HTMLParagraphElement>(null);
  useEffect(() => {
    if (feedback?.tone === "error") ref.current?.scrollIntoView?.({ block: "nearest" });
  }, [feedback]);
  if (feedback === null) return null;
  const error = feedback.tone === "error";
  return (
    <p
      ref={ref}
      role={error ? "alert" : "status"}
      className={cn(
        "rounded-lg border p-3 text-sm wrap-anywhere",
        error ? "border-destructive/40 bg-destructive/5 text-destructive" : "bg-muted",
      )}
    >
      {feedback.text}
    </p>
  );
}

type BodyProps = { allFlow: DeleteAllFlow; onDelete: (memoryId: number) => Promise<void> };

function MemoryBody({ allFlow, onDelete }: BodyProps) {
  const query = useMemories();
  if (query.isPending) return <p className="text-sm text-muted-foreground">불러오는 중…</p>;
  const reload = () => void query.refetch();
  const loadError = query.isError && (
    <LoadError message={query.error.message} fetching={query.isFetching} onReload={reload} />
  );
  if (query.data === undefined) return loadError;
  const { memories, limit } = query.data;
  return (
    <>
      {loadError}
      <MemorySummary count={memories.length} limit={limit} />
      {memories.length === 0 ? (
        <EmptyGuide />
      ) : (
        <>
          <DeleteAllArea flow={allFlow} count={memories.length} />
          <ul className="flex min-w-0 flex-col gap-2">
            {memories.map((memory) => (
              <MemoryItem
                key={memory.id}
                memory={memory}
                locked={allFlow.busy}
                onDelete={onDelete}
              />
            ))}
          </ul>
        </>
      )}
    </>
  );
}

type LoadErrorProps = { message: string; fetching: boolean; onReload: () => void };

function LoadError({ message, fetching, onReload }: LoadErrorProps) {
  return (
    <div className="flex flex-col gap-2 rounded-lg border border-destructive/40 bg-destructive/5 p-3">
      <p role="alert" className="text-sm text-destructive wrap-anywhere">
        {message}
      </p>
      <Button
        type="button"
        variant="outline"
        onClick={onReload}
        disabled={fetching}
        className={BIG_BUTTON}
      >
        {fetching ? "다시 불러오는 중…" : "다시 불러오기"}
      </Button>
    </div>
  );
}

function MemorySummary({ count, limit }: { count: number; limit: number }) {
  return (
    <div className="flex flex-col gap-1 text-sm">
      <p className="font-medium">{memoryCountText(count, limit)}</p>
      {count >= limit && (
        <p className="text-destructive">
          기억이 가득 찼습니다. 새로 기억하게 하려면 먼저 몇 개를 지워 주세요.
        </p>
      )}
      <p className="text-muted-foreground">
        지운 기억은 바로 다음 답변부터 에이전트가 쓰지 않습니다. 지운 기억은 되돌릴 수 없습니다.
        예전 대화에 같은 내용이 남아 있으면 그 대화를 이어갈 때 참고될 수 있으니, 그 대화도 지우려면
        대화 목록에서 삭제하세요.
      </p>
    </div>
  );
}

const GUIDE_LINES = [
  "채팅에서 '나 매운 거 못 먹어, 기억해'처럼 말하면 저장됩니다.",
  "내가 '기억해'라고 한 말만, 그 말을 그대로 근거로 남겨 저장합니다. 검색 결과·메일·메모의 글은 기억이 되지 않습니다.",
  "남의 말을 옮기거나 가정하는 말(만약 ~라면)은 저장하기 전에 다시 묻습니다.",
  "비밀번호·카드 번호는 저장하지 않습니다.",
];

function EmptyGuide() {
  return (
    <div className="flex flex-col gap-2 rounded-xl border bg-card p-4 text-card-foreground">
      <p className="text-base font-medium">아직 저장된 기억이 없습니다.</p>
      <ul className="flex list-disc flex-col gap-1 pl-5 text-sm text-muted-foreground">
        {GUIDE_LINES.map((line) => (
          <li key={line}>{line}</li>
        ))}
      </ul>
    </div>
  );
}

function DeleteAllArea({ flow, count }: { flow: DeleteAllFlow; count: number }) {
  if (flow.confirming) return <DeleteAllConfirm flow={flow} count={count} />;
  return (
    <Button
      type="button"
      variant="outline"
      onClick={flow.ask}
      className={cn(BIG_BUTTON, "text-destructive")}
    >
      전체 삭제
    </Button>
  );
}

function DeleteAllConfirm({ flow, count }: { flow: DeleteAllFlow; count: number }) {
  const boxRef = useRef<HTMLDivElement>(null);
  // 폰에서는 확인 영역이 가려질 수 있어 열릴 때 보이는 곳까지 스크롤한다(jsdom에는 없어서 ?.()).
  useEffect(() => {
    boxRef.current?.scrollIntoView?.({ block: "nearest" });
  }, []);
  return (
    <div
      ref={boxRef}
      className="flex flex-col gap-2 rounded-lg border border-destructive/40 bg-destructive/5 p-3"
    >
      <p className="text-sm font-medium text-destructive">기억 {count}개를 모두 지울까요?</p>
      <p className="text-sm">
        지운 기억은 되돌릴 수 없고, 바로 다음 답변부터 에이전트가 쓰지 않습니다. 예전 대화에 남은
        같은 내용은 지워지지 않습니다.
      </p>
      <div className="grid grid-cols-2 gap-2">
        <Button type="button" onClick={flow.submit} disabled={flow.busy} className={BIG_BUTTON}>
          모두 지우기
        </Button>
        <Button
          type="button"
          variant="outline"
          onClick={flow.cancel}
          disabled={flow.busy}
          className={BIG_BUTTON}
        >
          취소
        </Button>
      </div>
      {flow.busy && (
        <p className="flex items-center gap-2 text-sm text-muted-foreground">
          <LoaderCircle aria-hidden="true" className="size-4 shrink-0 animate-spin" />
          지우는 중…
        </p>
      )}
    </div>
  );
}

type ItemProps = {
  memory: Memory;
  locked: boolean;
  onDelete: (memoryId: number) => Promise<void>;
};

function MemoryItem({ memory, locked, onDelete }: ItemProps) {
  const [open, setOpen] = useState(false);
  const { busy, run } = useLocked();
  const contentId = `memory-${memory.id}-content`;
  const sourceId = `memory-${memory.id}-source`;
  return (
    <li className="flex min-w-0 flex-col gap-2 rounded-xl border bg-card p-3 text-card-foreground">
      <p id={contentId} className="whitespace-pre-wrap text-base wrap-anywhere">
        {memory.content}
      </p>
      {memory.source_quote && (
        <p className="text-sm text-muted-foreground wrap-anywhere">근거: “{memory.source_quote}”</p>
      )}
      <p className="text-xs text-muted-foreground">
        저장한 날짜 {formatMemoryDate(memory.created_at)}
      </p>
      <div className="grid grid-cols-2 gap-2">
        <Button
          type="button"
          variant="outline"
          aria-expanded={open}
          aria-controls={open ? sourceId : undefined}
          onClick={() => setOpen((value) => !value)}
          className={ITEM_BUTTON}
        >
          {open ? "원래 메시지 접기" : "원래 메시지 보기"}
        </Button>
        <Button
          type="button"
          variant="destructive"
          aria-describedby={contentId}
          disabled={busy || locked}
          onClick={() => run(() => onDelete(memory.id))}
          className={ITEM_BUTTON}
        >
          {busy ? "지우는 중…" : "삭제"}
        </Button>
      </div>
      {open && <SourceMessage id={sourceId} source={memory.source} />}
    </li>
  );
}

function SourceMessage({ id, source }: { id: string; source: Memory["source"] }) {
  return (
    <div id={id} className="flex min-w-0 flex-col gap-1 rounded-lg bg-muted p-3 text-sm">
      <p className="text-xs font-medium text-muted-foreground">
        이 기억을 만든 내 메시지 · {formatMessageTime(source.created_at)}
      </p>
      <p className="whitespace-pre-wrap wrap-anywhere">{source.content}</p>
      <Link
        href={conversationHref(source.conversation_id)}
        className={cn(buttonVariants({ variant: "link" }), "h-11 w-fit px-0 text-sm")}
      >
        이 대화로 가기
      </Link>
    </div>
  );
}
