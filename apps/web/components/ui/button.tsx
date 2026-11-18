// 공통 UI 부품: 버튼. shadcn(style "base-nova", components.json)이 Base UI Button 위에 만든 생성 코드다.
// 쓰는 곳: 화면 컴포넌트 대부분(ApprovalCard, ChatInput, LoginForm, ConversationSheet, PushSettings 등)과
//   이 폴더의 dialog·sheet 닫기 버튼. buttonVariants는 링크를 버튼 모양으로 그릴 때도 쓴다
//   (AutomationItems·MemoryList의 variant "link").
// 크기: 기본 높이는 32px(h-8)라 폰에서 누르기 작다. 화면 쪽에서 className으로 h-11(44px) 등을 덧붙여 키운다
//   (예: ConversationSheet의 size-11 닫기 버튼, DECISIONS 9단계 화면 점검 2026-11-17).
// 고칠 때 주의:
// - 이 폴더는 Biome 검사(biome.json)와 lib/theme.test.ts의 고정 색 검사에서 빠진다. 색은 globals.css 토큰만 쓴다.
// - variant·size를 바꾸거나 지우면 쓰는 곳 전체(Grep "components/ui/button", "buttonVariants")를 확인하고
//   명도 대비 e2e(e2e/dark-mode.spec.ts)와 폭 점검 e2e(e2e/widths.spec.ts의 44px 버튼 검사)를 돌린다.
// - `shadcn add button`으로 다시 받으면 이 파일(주석 포함)이 덮어써진다.
import { Button as ButtonPrimitive } from "@base-ui/react/button"
import { cva, type VariantProps } from "class-variance-authority"
import { cn } from "cn"

const buttonVariants = cva(
  "group/button inline-flex shrink-0 items-center justify-center rounded-lg border border-transparent bg-clip-padding text-sm font-medium whitespace-nowrap transition-all outline-none select-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 active:not-aria-[haspopup]:translate-y-px disabled:pointer-events-none disabled:opacity-50 aria-invalid:border-destructive aria-invalid:ring-3 aria-invalid:ring-destructive/20 dark:aria-invalid:border-destructive/50 dark:aria-invalid:ring-destructive/40 [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4",
  {
    variants: {
      variant: {
        default: "bg-primary text-primary-foreground hover:bg-primary/80",
        outline:
          "border-border bg-background hover:bg-muted hover:text-foreground aria-expanded:bg-muted aria-expanded:text-foreground dark:border-input dark:bg-input/30 dark:hover:bg-input/50",
        secondary:
          "bg-secondary text-secondary-foreground hover:bg-[color-mix(in_oklch,var(--secondary),var(--foreground)_5%)] aria-expanded:bg-secondary aria-expanded:text-secondary-foreground",
        ghost:
          "hover:bg-muted hover:text-foreground aria-expanded:bg-muted aria-expanded:text-foreground dark:hover:bg-muted/50",
        destructive:
          "bg-destructive/10 text-destructive hover:bg-destructive/20 focus-visible:border-destructive/40 focus-visible:ring-destructive/20 dark:bg-destructive/20 dark:hover:bg-destructive/30 dark:focus-visible:ring-destructive/40",
        link: "text-primary underline-offset-4 hover:underline",
      },
      size: {
        default:
          "h-8 gap-1.5 px-2.5 has-data-[icon=inline-end]:pr-2 has-data-[icon=inline-start]:pl-2",
        xs: "h-6 gap-1 rounded-[min(var(--radius-md),10px)] px-2 text-xs in-data-[slot=button-group]:rounded-lg has-data-[icon=inline-end]:pr-1.5 has-data-[icon=inline-start]:pl-1.5 [&_svg:not([class*='size-'])]:size-3",
        sm: "h-7 gap-1 rounded-[min(var(--radius-md),12px)] px-2.5 text-[0.8rem] in-data-[slot=button-group]:rounded-lg has-data-[icon=inline-end]:pr-1.5 has-data-[icon=inline-start]:pl-1.5 [&_svg:not([class*='size-'])]:size-3.5",
        lg: "h-9 gap-1.5 px-2.5 has-data-[icon=inline-end]:pr-2 has-data-[icon=inline-start]:pl-2",
        icon: "size-8",
        "icon-xs":
          "size-6 rounded-[min(var(--radius-md),10px)] in-data-[slot=button-group]:rounded-lg [&_svg:not([class*='size-'])]:size-3",
        "icon-sm":
          "size-7 rounded-[min(var(--radius-md),12px)] in-data-[slot=button-group]:rounded-lg",
        "icon-lg": "size-9",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  }
)

/**
 * 버튼(Base UI Button에 data-slot과 variant 클래스를 입힌 것).
 * props: variant(기본 default), size(기본 default), className(뒤에 붙여 기본 클래스를 덮어씀), 나머지 Base UI Button 속성
 * (render로 다른 요소로 그릴 수 있다: dialog·sheet의 Close가 이 버튼을 render로 쓴다).
 */
function Button({
  className,
  variant = "default",
  size = "default",
  ...props
}: ButtonPrimitive.Props & VariantProps<typeof buttonVariants>) {
  return (
    <ButtonPrimitive
      data-slot="button"
      className={cn(buttonVariants({ variant, size, className }))}
      {...props}
    />
  )
}

export { Button, buttonVariants }
