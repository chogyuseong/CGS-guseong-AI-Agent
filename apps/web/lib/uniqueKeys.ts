/**
 * 목록 항목에 겹치지 않는 React key를 붙인다(2026-11-18).
 *
 * - 쓰는 곳: components/ExternalContent.tsx(출처 주소), components/SuggestionChips.tsx(제안).
 * - 왜: 서버가 같은 값(같은 출처 주소, 같은 제안)을 두 번 보낼 수 있다. 값만 key로 쓰면 React가 "same key" 경고를 내고
 *   항목이 빠지거나 겹쳐 보일 수 있다. 배열 순번을 key로 쓰는 것은 Biome(noArrayIndexKey)이 막으므로,
 *   "이 값의 몇 번째 등장인지"를 붙인다. 값이 다르면 key는 값 그대로라 순서가 바뀌어도 안정적이다.
 * - 고칠 때 주의: key 모양을 바꿔도 화면에는 보이지 않는다. 테스트는 lib/uniqueKeys.test.ts와 두 컴포넌트 테스트.
 */
export function withUniqueKeys<T>(items: readonly T[], keyOf: (item: T) => string) {
  const seen = new Map<string, number>();
  return items.map((item) => {
    const base = keyOf(item);
    const count = (seen.get(base) ?? 0) + 1;
    seen.set(base, count);
    return { item, key: count === 1 ? base : `${base}#${count}` };
  });
}
