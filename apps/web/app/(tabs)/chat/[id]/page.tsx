import { notFound } from "next/navigation";
import { ChatScreen } from "@/components/ChatScreen";

// /chat/[id]: [id] 폴더는 주소의 이 자리를 값으로 받는다. (Django urls.py의 <int:id>)
// Next 16에서 params는 Promise라서 await로 꺼낸다(설치본 문서 dynamic-routes.md).
export default async function ChatDetailPage({ params }: PageProps<"/chat/[id]">) {
  const { id } = await params;
  const conversationId = Number(id);
  if (!Number.isInteger(conversationId) || conversationId <= 0) notFound();
  return <ChatScreen conversationId={conversationId} />;
}
