import { LoginForm } from "@/components/LoginForm";

// /login 화면. 탭 막대가 없는 단독 화면이라 (tabs) 묶음 밖에 둔다.
export default function LoginPage() {
  return (
    <main className="flex min-h-dvh flex-col items-center justify-center p-4 pt-[max(1rem,env(safe-area-inset-top))] pb-[max(1rem,env(safe-area-inset-bottom))]">
      <LoginForm />
    </main>
  );
}
