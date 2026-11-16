"use client";

import {
  Brain,
  ListChecks,
  type LucideIcon,
  MessageCircle,
  Settings,
  Workflow,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";

// type은 데이터 모양을 정하는 선언이다. 실행 코드는 없다. (Python의 TypedDict와 비슷)
type TabItem = {
  href: string;
  label: string;
  icon: LucideIcon;
};

// 하단 탭 5개. 순서는 명세의 [화면] 절을 따른다.
export const TAB_ITEMS: TabItem[] = [
  { href: "/chat", label: "채팅", icon: MessageCircle },
  { href: "/tasks", label: "작업 기록", icon: ListChecks },
  { href: "/automation", label: "자동화", icon: Workflow },
  { href: "/memory", label: "기억", icon: Brain },
  { href: "/settings", label: "설정", icon: Settings },
];

function isActive(pathname: string, href: string): boolean {
  return pathname === href || pathname.startsWith(`${href}/`);
}

// 화면 아래에 고정된 탭 막대. 아이폰 홈 바에 가리지 않게 safe-area 만큼 아래 여백을 더한다.
// 컴포넌트는 화면 조각을 돌려주는 함수다. (Django의 include 템플릿 + 그 템플릿을 그리는 함수)
export function BottomTabs() {
  // usePathname: 지금 주소를 읽는 훅. (Django의 request.path)
  const pathname = usePathname();
  return (
    <nav aria-label="하단 탭" className="border-t bg-background pb-[env(safe-area-inset-bottom)]">
      <ul className="grid grid-cols-5">
        {/* map은 배열을 화면 조각 배열로 바꾼다. (템플릿의 {% for %}) key는 각 항목을 구분하는 값이다. */}
        {TAB_ITEMS.map(({ href, label, icon: Icon }) => {
          const active = isActive(pathname, href);
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
