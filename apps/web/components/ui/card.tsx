// 공통 UI 부품: 카드 틀(Card, CardHeader, CardTitle, CardDescription, CardAction, CardContent, CardFooter).
// shadcn(style "base-nova", components.json)이 만든 생성 코드이고 Base UI를 쓰지 않는 순수 div 묶음이다.
// 쓰는 곳: 지금은 LoginForm(로그인 카드)만. 다른 화면의 카드 모양은 각 컴포넌트가 rounded-xl border bg-card로 직접 그린다
//   (예: 설정 탭의 SettingsLink).
// 안쪽 여백은 CSS 변수 --card-spacing(기본 16px, size="sm"이면 12px) 하나로 머리·본문·바닥이 함께 맞춰진다.
// 고칠 때 주의: 이 폴더는 Biome 검사와 lib/theme.test.ts의 고정 색 검사에서 빠진다. 색은 globals.css 토큰만 쓴다.
//   바꾸면 e2e/dark-mode.spec.ts(로그인 화면 대비)를 돌린다. `shadcn add card`로 다시 받으면 주석까지 덮어써진다.
import * as React from "react"
import { cn } from "cn"

/**
 * 카드 바깥 틀. props: size("default" | "sm", 여백 크기), className, 나머지 div 속성.
 * data-size를 남겨 하위 부품(CardTitle 등)이 group-data-[size=sm]/card로 크기에 맞춘다.
 */
function Card({
  className,
  size = "default",
  ...props
}: React.ComponentProps<"div"> & { size?: "default" | "sm" }) {
  return (
    <div
      data-slot="card"
      data-size={size}
      className={cn(
        "group/card flex flex-col gap-(--card-spacing) overflow-hidden rounded-xl bg-card py-(--card-spacing) text-sm text-card-foreground ring-1 ring-foreground/10 [--card-spacing:--spacing(4)] has-data-[slot=card-footer]:pb-0 has-[>img:first-child]:pt-0 data-[size=sm]:[--card-spacing:--spacing(3)] data-[size=sm]:has-data-[slot=card-footer]:pb-0 *:[img:first-child]:rounded-t-xl *:[img:last-child]:rounded-b-xl",
        className
      )}
      {...props}
    />
  )
}

function CardHeader({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="card-header"
      className={cn(
        "group/card-header @container/card-header grid auto-rows-min items-start gap-1 rounded-t-xl px-(--card-spacing) has-data-[slot=card-action]:grid-cols-[1fr_auto] has-data-[slot=card-description]:grid-rows-[auto_auto] [.border-b]:pb-(--card-spacing)",
        className
      )}
      {...props}
    />
  )
}

function CardTitle({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="card-title"
      className={cn(
        "font-heading text-base leading-snug font-medium group-data-[size=sm]/card:text-sm",
        className
      )}
      {...props}
    />
  )
}

function CardDescription({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="card-description"
      className={cn("text-sm text-muted-foreground", className)}
      {...props}
    />
  )
}

// 머리줄 오른쪽 위에 붙는 동작 자리(버튼 등). CardHeader가 이 부품이 있으면 두 칸 격자로 바뀐다.
function CardAction({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="card-action"
      className={cn(
        "col-start-2 row-span-2 row-start-1 self-start justify-self-end",
        className
      )}
      {...props}
    />
  )
}

function CardContent({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="card-content"
      className={cn("px-(--card-spacing)", className)}
      {...props}
    />
  )
}

function CardFooter({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="card-footer"
      className={cn(
        "flex items-center rounded-b-xl border-t bg-muted/50 p-(--card-spacing)",
        className
      )}
      {...props}
    />
  )
}

export {
  Card,
  CardHeader,
  CardFooter,
  CardTitle,
  CardAction,
  CardDescription,
  CardContent,
}
