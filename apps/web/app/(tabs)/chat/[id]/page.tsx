// 맡은 일: 대화방 화면(/chat/{대화 id}). 주소의 id를 검사해 ChatScreen(메시지·입력창·진행 상황·승인 카드)에 넘긴다.
// 들어오는 곳: ChatHome(최근 대화), 대화 목록 시트(ConversationSheet), 기억 탭의 '이 대화로 가기'(lib/memories.ts
//   conversationHref), 자동화 탭의 마지막 실행 결과 링크, 푸시 알림 클릭(서버가 "/chat/{대화 id}"를 보낸다:
//   apps/api/app/agent/runner.py, apps/api/app/automation/check.py).
// 고칠 때 주의: 이 주소 모양을 바꾸면 위 진입점, 서버 푸시 URL과 apps/api/app/push.py의 is_internal_url,
//   public/sw.js와 lib/sw.test.ts, e2e(automation·memory·conversation-delete·dark-mode·widths spec의 "/chat/숫자")를 함께 고친다.
import { notFound } from "next/navigation";
import { ChatScreen } from "@/components/ChatScreen";

// /chat/[id]: [id] 폴더는 주소의 이 자리를 값으로 받는다. (Django urls.py의 <int:id>)
// Next 16에서 params는 Promise라서 await로 꺼낸다(설치본 문서 dynamic-routes.md).
/**
 * 주소의 id가 양의 정수가 아니면("abc", "0", "1.5") API를 부르지 않고 바로 404 화면을 보여 준다.
 * 숫자 모양이지만 없는 대화·남의 대화인지는 여기서 가리지 않는다. 서버가 둘 다 404로 답하고(존재 여부를 숨김,
 * DECISIONS 2026-11-16) ChatScreen은 메시지 불러오기 오류 문구를 보여 준다.
 */
export default async function ChatDetailPage({ params }: PageProps<"/chat/[id]">) {
  const { id } = await params;
  const conversationId = Number(id);
  if (!Number.isInteger(conversationId) || conversationId <= 0) notFound();
  return <ChatScreen conversationId={conversationId} />;
}
