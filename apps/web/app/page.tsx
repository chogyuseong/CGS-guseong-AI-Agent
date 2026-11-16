import { redirect } from "next/navigation";

// "/"로 들어오면 채팅 화면으로 보낸다. 로그인 여부는 (tabs) 레이아웃의 AuthGuard가 확인한다.
// (Django의 RedirectView와 같은 역할)
export default function Home() {
  redirect("/chat");
}
