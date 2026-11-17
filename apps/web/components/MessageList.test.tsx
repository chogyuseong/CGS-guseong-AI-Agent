import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { Message } from "@/lib/chat";
import { MessageList } from "./MessageList";

// TaskSteps는 서버에서 task를 가져오는 컴포넌트다. 여기서는 어떤 task_id로 불렸는지만 확인하도록 가짜로 바꾼다.
vi.mock("./TaskSteps", () => ({
  TaskSteps: ({ taskId }: { taskId: number }) => <div>task {taskId}의 단계</div>,
}));
vi.mock("./PendingTask", () => ({
  PendingTask: ({ taskId }: { taskId: number }) => <div>task {taskId} 진행 상황</div>,
}));

type MessageFields = Pick<Message, "id" | "role" | "content" | "task_id" | "created_at"> &
  Partial<Pick<Message, "origin" | "sources" | "suggestions">>;

// 서버 MessageOut 모양. origin을 주지 않으면 채팅 메시지(chat), sources는 빈 목록.
function message(fields: MessageFields): Message {
  return { origin: "chat", sources: [], suggestions: [], ...fields };
}

const messages: Message[] = [
  message({
    id: 1,
    role: "user",
    content: "우유 사기 할 일에 추가해줘",
    task_id: 10,
    created_at: "2026-11-16T12:00:00Z",
  }),
  message({
    id: 2,
    role: "assistant",
    content: "할 일에 추가했습니다.",
    task_id: 10,
    created_at: "2026-11-16T12:00:03Z",
  }),
];

describe("MessageList", () => {
  it("내 메시지와 답변을 순서대로 보여 준다", () => {
    render(<MessageList messages={messages} />);
    const items = screen.getAllByRole("listitem");

    expect(items).toHaveLength(2);
    expect(items[0]).toHaveTextContent("나");
    expect(items[0]).toHaveTextContent("우유 사기 할 일에 추가해줘");
    expect(items[1]).toHaveTextContent("마이에이전트");
    expect(items[1]).toHaveTextContent("할 일에 추가했습니다.");
  });

  it("답변에 연결된 task의 단계만 붙인다(내 메시지에는 붙이지 않는다)", () => {
    render(<MessageList messages={messages} />);
    expect(screen.getAllByText("task 10의 단계")).toHaveLength(1);
  });

  it("보내는 중인 내 메시지를 목록 끝에 보여 준다", () => {
    render(<MessageList messages={messages} pendingText="내일 날씨 알려줘" />);
    const items = screen.getAllByRole("listitem");

    expect(items).toHaveLength(3);
    expect(items[2]).toHaveTextContent("내일 날씨 알려줘");
    expect(items[2]).toHaveTextContent("보내는 중");
  });

  it("답변이 아직 없는 내 메시지의 task는 답변 자리에 진행 상황(승인 카드 포함)을 붙인다", () => {
    const waiting: Message[] = [
      ...messages,
      message({
        id: 3,
        role: "user",
        content: "내일 저녁 7시에 저녁 약속 잡아줘",
        task_id: 11,
        created_at: "2026-11-16T12:01:00Z",
      }),
    ];
    render(<MessageList messages={waiting} />);
    const items = screen.getAllByRole("listitem");

    expect(items).toHaveLength(4);
    expect(items[3]).toHaveTextContent("마이에이전트");
    expect(items[3]).toHaveTextContent("task 11 진행 상황");
    expect(screen.queryByText("task 10 진행 상황")).not.toBeInTheDocument();
  });

  it("메시지가 없으면 시작 안내를 보여 준다", () => {
    render(<MessageList messages={[]} />);
    expect(screen.getByText("대화를 시작해 보세요.")).toBeInTheDocument();
  });
});

// 메시지 말풍선(목록의 바로 아래 항목)만. 출처 목록의 항목(li)은 세지 않는다.
function bubbles(): HTMLElement[] {
  return [...screen.getByRole("list", { name: "메시지 목록" }).children] as HTMLElement[];
}

describe("MessageList: '예약 작업' 대화방", () => {
  const roomMessages: Message[] = [
    message({
      id: 21,
      role: "assistant",
      content: "오늘 일정은 2개입니다.",
      suggestions: [
        { title: "우산 챙기기", proposed_user_message: "우산 챙기기 해줘", reason: "" },
      ],
      task_id: 30,
      created_at: "2026-11-16T22:31:00Z",
      origin: "schedule",
    }),
    message({
      id: 22,
      role: "assistant",
      content: "[조건 감시] 내일 서울 비",
      suggestions: [{ title: "감시 알림 안의 제안", proposed_user_message: "x", reason: "" }],
      task_id: null,
      created_at: "2026-11-16T23:00:00Z",
      origin: "watch",
      sources: ["https://open-meteo.com/", "javascript:alert(1)"],
    }),
    message({
      id: 23,
      role: "user",
      content: "고마워",
      task_id: 31,
      created_at: "2026-11-16T23:01:00Z",
    }),
    message({
      id: 24,
      role: "assistant",
      content: "채팅 답변",
      suggestions: [
        {
          title: "채팅 답변 안의 제안",
          proposed_user_message: "채팅 답변 안의 제안 해줘",
          reason: "",
        },
      ],
      task_id: 31,
      created_at: "2026-11-16T23:01:05Z",
    }),
  ];

  it("예약 작업 결과에만 제안 버튼을 붙이고, 감시 알림·채팅 답변에는 붙이지 않는다", () => {
    render(<MessageList messages={roomMessages} automationRoom onSuggestion={vi.fn()} />);

    expect(
      screen.getByRole("button", { name: "우산 챙기기: 입력창에 채우기" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "감시 알림 안의 제안: 입력창에 채우기" }),
    ).toBeNull();
    expect(
      screen.queryByRole("button", { name: "채팅 답변 안의 제안: 입력창에 채우기" }),
    ).toBeNull();
  });

  it("보낼 함수(onSuggestion)를 받지 않은 대화에서는 제안 버튼을 그리지 않는다", () => {
    render(<MessageList messages={roomMessages} automationRoom />);
    expect(screen.queryByRole("button", { name: "우산 챙기기: 입력창에 채우기" })).toBeNull();
  });

  it("다른 대화에서는 예약 작업 결과라도 제안 버튼과 '예약 작업 결과' 표시를 그리지 않는다", () => {
    render(<MessageList messages={roomMessages} onSuggestion={vi.fn()} />);
    expect(screen.queryByRole("button", { name: "우산 챙기기: 입력창에 채우기" })).toBeNull();
    expect(bubbles()[0]).not.toHaveTextContent("예약 작업 결과");
  });

  it("자동으로 생긴 메시지에는 어디서 왔는지 표시한다(서버 origin 기준)", () => {
    render(<MessageList messages={roomMessages} automationRoom />);
    const items = bubbles();
    expect(items[0]).toHaveTextContent("예약 작업 결과");
    expect(items[1]).toHaveTextContent("조건 감시 알림");
    expect(items[3]).not.toHaveTextContent("예약 작업 결과");
  });

  it("내 메시지가 없는 답변이라도 origin이 chat이면 예약 작업 결과로 보지 않는다", () => {
    const orphan = message({
      id: 25,
      role: "assistant",
      content: "채팅 답변",
      suggestions: [{ title: "채팅 답변", proposed_user_message: "채팅 답변 해줘", reason: "" }],
      task_id: 32,
      created_at: "2026-11-16T23:02:00Z",
    });
    render(<MessageList messages={[orphan]} automationRoom onSuggestion={vi.fn()} />);
    expect(bubbles()[0]).not.toHaveTextContent("예약 작업 결과");
    expect(screen.queryByRole("button", { name: "채팅 답변: 입력창에 채우기" })).toBeNull();
  });

  it("감시 알림의 출처는 http·https만 새 탭 링크로, 그 밖은 글자로만 보여 준다", () => {
    render(<MessageList messages={roomMessages} automationRoom />);
    const watchItem = bubbles().at(1);
    if (!watchItem) throw new Error("감시 알림 말풍선을 찾지 못했습니다");
    const sources = within(watchItem).getByRole("list", { name: "출처" });
    const link = within(sources).getByRole("link", { name: "https://open-meteo.com/" });
    expect(link).toHaveAttribute("href", "https://open-meteo.com/");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    expect(within(sources).getByText("javascript:alert(1)").closest("a")).toBeNull();
    expect(within(sources).getAllByRole("link")).toHaveLength(1);
  });

  it("출처가 없는 메시지에는 출처 목록을 그리지 않는다", () => {
    render(<MessageList messages={roomMessages} automationRoom />);
    expect(screen.getAllByRole("list", { name: "출처" })).toHaveLength(1);
  });

  it("답변이 아직 없는 예약 task는 목록 끝에 진행 상황을 붙인다(내 메시지가 없어도)", () => {
    render(<MessageList messages={roomMessages} automationRoom openTaskIds={[40]} />);
    const items = bubbles();
    expect(items.at(-1)).toHaveTextContent("task 40 진행 상황");
    expect(items.at(-1)).toHaveTextContent("예약 작업 실행 중");
  });
});
