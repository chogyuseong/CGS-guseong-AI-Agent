// 공통 UI 부품: 배지(상태·권한 같은 짧은 표시). shadcn(style "base-nova", components.json)이 Base UI 위에 만든 생성 코드다.
// 쓰는 곳: ApprovalCard, AutomationItems, GoogleConnection, MailPermission, PushSettings, TaskList
//   (variant는 secondary·destructive를 직접 주거나 상태에 따라 고른다).
// 색은 globals.css의 토큰(primary, secondary, destructive, muted 등)만 쓰고, 어두운 화면은 dark: 변형
//   (globals.css의 @custom-variant dark = prefers-color-scheme)으로 바뀐다.
// 고칠 때 주의:
// - 이 폴더는 Biome 검사(biome.json)와 lib/theme.test.ts의 고정 색 검사에서 빠진다. 대신 고정 색(text-green-600 등)을
//   넣지 말고 토큰만 쓴다(DECISIONS 9단계 보강 2026-11-18 상태 색 토큰).
// - variant를 바꾸거나 지우면 위 쓰는 곳 전체를 확인하고 명도 대비 e2e(e2e/dark-mode.spec.ts, 어둠·밝음 두 번)를 돌린다.
// - `shadcn add badge`로 다시 받으면 이 파일(주석 포함)이 덮어써진다.
import { mergeProps } from "@base-ui/react/merge-props"
import { useRender } from "@base-ui/react/use-render"
import { cva, type VariantProps } from "class-variance-authority"
import { cn } from "cn"

const badgeVariants = cva(
  "group/badge inline-flex h-5 w-fit shrink-0 items-center justify-center gap-1 overflow-hidden rounded-4xl border border-transparent px-2 py-0.5 text-xs font-medium whitespace-nowrap transition-all focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 has-data-[icon=inline-end]:pr-1.5 has-data-[icon=inline-start]:pl-1.5 aria-invalid:border-destructive aria-invalid:ring-destructive/20 dark:aria-invalid:ring-destructive/40 [&>svg]:pointer-events-none [&>svg]:size-3!",
  {
    variants: {
      variant: {
        default: "bg-primary text-primary-foreground [a]:hover:bg-primary/80",
        secondary:
          "bg-secondary text-secondary-foreground [a]:hover:bg-secondary/80",
        destructive:
          "bg-destructive/10 text-destructive focus-visible:ring-destructive/20 dark:bg-destructive/20 dark:focus-visible:ring-destructive/40 [a]:hover:bg-destructive/20",
        outline:
          "border-border text-foreground [a]:hover:bg-muted [a]:hover:text-muted-foreground",
        ghost:
          "hover:bg-muted hover:text-muted-foreground dark:hover:bg-muted/50",
        link: "text-primary underline-offset-4 hover:underline",
      },
    },
    defaultVariants: {
      variant: "default",
    },
  }
)

/**
 * 배지. 기본은 <span>이고 render로 다른 요소(예: 링크)로 바꿔 그릴 수 있다(Base UI useRender).
 * props: variant(색 종류, 기본 default), className(뒤에 붙여 기본 클래스를 덮어씀, cn이 Tailwind 충돌을 정리), 나머지 span 속성.
 */
function Badge({
  className,
  variant = "default",
  render,
  ...props
}: useRender.ComponentProps<"span"> & VariantProps<typeof badgeVariants>) {
  return useRender({
    defaultTagName: "span",
    props: mergeProps<"span">(
      {
        className: cn(badgeVariants({ variant }), className),
      },
      props
    ),
    render,
    state: {
      slot: "badge",
      variant,
    },
  })
}

export { Badge, badgeVariants }
