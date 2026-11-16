import { render, screen } from "@testing-library/react";
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

const messages: Message[] = [
  {
    id: 1,
    role: "user",
    content: "우유 사기 할 일에 추가해줘",
    task_id: 10,
    created_at: "2026-11-16T12:00:00Z",
  },
  {
    id: 2,
    role: "assistant",
    content: "할 일에 추가했습니다.",
    task_id: 10,
    created_at: "2026-11-16T12:00:03Z",
  },
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
      {
        id: 3,
        role: "user",
        content: "내일 저녁 7시에 저녁 약속 잡아줘",
        task_id: 11,
        created_at: "2026-11-16T12:01:00Z",
      },
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

describe("MessageList: '예약 작업' 대화방", () => {
  const roomMessages: Message[] = [
    {
      id: 21,
      role: "assistant",
      content: "오늘 일정은 2개입니다.\n[제안] 우산 챙기라고 할 일에 추가해줘",
      task_id: 30,
      created_at: "2026-11-16T22:31:00Z",
    },
    {
      id: 22,
      role: "assistant",
      content: "[조건 감시] 내일 서울 비\n[제안] 감시 알림 안의 제안",
      task_id: null,
      created_at: "2026-11-16T23:00:00Z",
    },
    {
      id: 23,
      role: "user",
      content: "고마워",
      task_id: 31,
      created_at: "2026-11-16T23:01:00Z",
    },
    {
      id: 24,
      role: "assistant",
      content: "[제안] 채팅 답변 안의 제안",
      task_id: 31,
      created_at: "2026-11-16T23:01:05Z",
    },
  ];

  it("예약 작업 결과에만 [제안] 버튼을 붙이고, 감시 알림·채팅 답변에는 붙이지 않는다", () => {
    render(<MessageList messages={roomMessages} automationRoom onSuggestion={vi.fn()} />);

    expect(
      screen.getByRole("button", { name: "우산 챙기라고 할 일에 추가해줘" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "감시 알림 안의 제안" })).toBeNull();
    expect(screen.queryByRole("button", { name: "채팅 답변 안의 제안" })).toBeNull();
  });

  it("보낼 함수(onSuggestion)를 받지 않은 대화에서는 [제안] 버튼을 그리지 않는다", () => {
    render(<MessageList messages={roomMessages} automationRoom />);
    expect(screen.queryByRole("button", { name: "우산 챙기라고 할 일에 추가해줘" })).toBeNull();
  });

  it("다른 대화에서는 내 메시지 없는 답변이라도 [제안] 버튼을 그리지 않는다", () => {
    render(<MessageList messages={roomMessages} onSuggestion={vi.fn()} />);
    expect(screen.queryByRole("button", { name: "우산 챙기라고 할 일에 추가해줘" })).toBeNull();
    expect(screen.getAllByRole("listitem")[0]).not.toHaveTextContent("예약 작업 결과");
  });

  it("자동으로 생긴 메시지에는 어디서 왔는지 표시한다", () => {
    render(<MessageList messages={roomMessages} automationRoom />);
    const items = screen.getAllByRole("listitem");
    expect(items[0]).toHaveTextContent("예약 작업 결과");
    expect(items[1]).toHaveTextContent("조건 감시 알림");
    expect(items[3]).not.toHaveTextContent("예약 작업 결과");
  });

  it("답변이 아직 없는 예약 task는 목록 끝에 진행 상황을 붙인다(내 메시지가 없어도)", () => {
    render(<MessageList messages={roomMessages} automationRoom openTaskIds={[40]} />);
    const items = screen.getAllByRole("listitem");
    expect(items.at(-1)).toHaveTextContent("task 40 진행 상황");
    expect(items.at(-1)).toHaveTextContent("예약 작업 실행 중");
  });
});
