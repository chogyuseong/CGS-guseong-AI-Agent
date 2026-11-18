// className 합치기 도우미 cn을 "cn" 패키지에서 그대로 다시 내보낸다.
// 앱 컴포넌트(BottomTabs, MemoryList, UsageReport 등)는 "@/lib/utils"의 cn을 쓰고, components/ui/*(shadcn 생성 코드)는
// "cn" 패키지를 직접 가져온다. components.json의 aliases.utils가 이 경로("@/lib/utils")를 가리킨다.
// 고칠 때 주의: 이 파일을 옮기거나 이름을 바꾸면 components.json의 aliases.utils와 가져다 쓰는 컴포넌트를 같이 고친다.
export { cn } from "cn";
