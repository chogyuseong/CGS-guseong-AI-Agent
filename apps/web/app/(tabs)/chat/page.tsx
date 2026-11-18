// 맡은 일: /chat(하단 탭 '채팅'의 주소). 화면을 직접 그리지 않고 ChatHome에 맡긴다.
// ChatHome은 최근 대화(서버가 최근 활동순으로 줌)의 /chat/{id}로, 대화가 없으면 하나 만든 뒤 그 주소로 보낸다.
// 들어오는 곳: "/"(app/page.tsx), PWA start_url, 로그인 직후(LoginForm), 주소가 안전하지 않은 푸시 알림(sw.js의 DEFAULT_PATH),
//   대화 목록 시트에서 지금 대화를 지웠을 때(ConversationSheet).
// 고칠 때 주의: 경로를 바꾸면 위 진입점 전부와 BottomTabs, 서버의 푸시 URL("/chat/{id}", apps/api/app/push.py 검사)을 함께 본다.
import { ChatHome } from "@/components/ChatHome";

// /chat: 최근 대화(없으면 새 대화)의 /chat/[id]로 보낸다.
export default function ChatPage() {
  return <ChatHome />;
}
