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
