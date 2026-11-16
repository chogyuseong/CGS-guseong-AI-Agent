// 아직 기능이 없는 탭의 빈 화면. 각 기능은 명세의 단계에서 채운다.
// props는 컴포넌트에 넘기는 인자다. (템플릿 include에 with로 넘기는 값)
export function EmptyScreen({ title }: { title: string }) {
  return (
    <section className="flex flex-1 flex-col gap-2 overflow-y-auto p-4">
      <h1 className="text-lg font-semibold">{title}</h1>
      <p className="text-sm text-muted-foreground">아직 준비 중인 화면입니다.</p>
    </section>
  );
}
