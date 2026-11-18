// 맡은 일: 주소 "/"(앱 첫 주소)를 채팅 화면으로 넘긴다. 이 파일은 화면을 그리지 않는다.
// 이어지는 흐름: /chat(ChatHome) → 가장 최근 대화 /chat/{id}(없으면 새로 만든 뒤 이동).
// 고칠 때 주의: 첫 화면을 바꾸면 app/manifest.ts의 start_url("/chat", lib/pwa.test.ts가 확인),
//   LoginForm의 로그인 뒤 이동("/chat"), public/sw.js의 DEFAULT_PATH("/chat")도 같이 볼지 정한다.
import { redirect } from "next/navigation";

// "/"로 들어오면 채팅 화면으로 보낸다. 로그인 여부는 (tabs) 레이아웃의 AuthGuard가 확인한다.
// (Django의 RedirectView와 같은 역할)
export default function Home() {
  redirect("/chat");
}
