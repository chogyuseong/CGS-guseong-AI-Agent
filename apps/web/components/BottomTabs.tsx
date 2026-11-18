"use client";

import {
  Brain,
  CircleCheck,
  ListChecks,
  type LucideIcon,
  MessageCircle,
  Settings,
  Workflow,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";

// 화면 아래 고정 탭 막대(채팅·할 일·작업 기록·자동화·기억·설정).
//
// 쓰는 곳: app/(tabs)/layout.tsx(AuthGuard 안). TAB_ITEMS는 내보내지만 지금 다른 파일에서 가져다 쓰지 않는다.
// 근거: 명세 [화면] 절의 탭 순서, DECISIONS 2026-11-17 "하단 탭은 /connections에서도 '설정'을 현재 탭으로 표시"
//   (/usage도 설정 탭의 링크로 들어가 같은 방식), 폭 점검 결정(넓은 화면에서 max-w-3xl로 모음).
//
// 고칠 때 주의:
// - href는 app/(tabs) 아래 폴더 이름과 같아야 한다. 탭 안 화면을 새로 만들면 children에 넣어야 그 화면에서 탭이 켜진다.
// - 탭 이름 글자는 e2e/login.spec.ts(TAB_LABELS)와 components/BottomTabs.test.tsx가 찾는다.
// - 탭 개수를 바꾸면 아래 ul의 grid-cols-N도 같이 바꾼다.
// 변경 이력:
// - 2026-11-18 '할 일' 탭 추가(요구사항 밖, 사용자 결정 DECISIONS 2026-11-18). 장보며 자주 여는 화면이라
//   엄지가 닿기 쉬운 왼쪽(채팅 다음)에 뒀다. 다섯 → 여섯 칸(360px에서 칸당 60px, 글자는 text-xs 그대로).

// type은 데이터 모양을 정하는 선언이다. 실행 코드는 없다. (Python의 TypedDict와 비슷)
type TabItem = {
  href: string;
  label: string;
  icon: LucideIcon;
  // 이 탭 안에서 들어가는 다른 주소(예: 설정 → /connections). 그 화면에서도 이 탭을 현재 탭으로 표시한다.
  children?: string[];
};

// 하단 탭 6개. '할 일'을 뺀 순서는 명세의 [화면] 절을 따른다.
export const TAB_ITEMS: TabItem[] = [
  { href: "/chat", label: "채팅", icon: MessageCircle },
  { href: "/todos", label: "할 일", icon: CircleCheck },
  { href: "/tasks", label: "작업 기록", icon: ListChecks },
  { href: "/automation", label: "자동화", icon: Workflow },
  { href: "/memory", label: "기억", icon: Brain },
  { href: "/settings", label: "설정", icon: Settings, children: ["/connections", "/usage"] },
];

// 정확히 같거나 그 아래 주소면 맞는 것으로 본다(/chat/12도 채팅 탭). "/" 경계를 붙여 /chatx 같은 주소는 빼낸다.
function matches(pathname: string, href: string): boolean {
  return pathname === href || pathname.startsWith(`${href}/`);
}

function isActive(pathname: string, item: TabItem): boolean {
  return [item.href, ...(item.children ?? [])].some((href) => matches(pathname, href));
}

// 화면 아래에 고정된 탭 막대. 아이폰 홈 바에 가리지 않게 safe-area 만큼 아래 여백을 더한다.
// 넓은 화면에서는 탭 여섯 개를 본문과 같은 폭(max-w-3xl)으로 가운데에 모은다(경계선은 화면 끝까지).
// 컴포넌트는 화면 조각을 돌려주는 함수다. (Django의 include 템플릿 + 그 템플릿을 그리는 함수)
export function BottomTabs() {
  // usePathname: 지금 주소를 읽는 훅. (Django의 request.path)
  const pathname = usePathname();
  return (
    <nav aria-label="하단 탭" className="border-t bg-background pb-[env(safe-area-inset-bottom)]">
      <ul className="mx-auto grid max-w-3xl grid-cols-6">
        {/* map은 배열을 화면 조각 배열로 바꾼다. (템플릿의 {% for %}) key는 각 항목을 구분하는 값이다. */}
        {TAB_ITEMS.map((item) => {
          const { href, label, icon: Icon } = item;
          const active = isActive(pathname, item);
          return (
            <li key={href}>
              <Link
                href={href}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "flex min-h-14 flex-col items-center justify-center gap-0.5 text-xs",
                  active ? "text-foreground font-semibold" : "text-muted-foreground",
                )}
              >
                <Icon aria-hidden="true" className="size-5" />
                <span>{label}</span>
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
