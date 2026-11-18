import { describe, expect, it } from "vitest";
import { withUniqueKeys } from "./uniqueKeys";

describe("withUniqueKeys", () => {
  it("같은 값이 여러 번 와도 key가 겹치지 않는다", () => {
    const keys = withUniqueKeys(["a", "b", "a", "a"], (item) => item).map((entry) => entry.key);
    expect(new Set(keys).size).toBe(4);
    expect(keys).toEqual(["a", "b", "a#2", "a#3"]);
  });

  it("값이 모두 다르면 key는 값 그대로다", () => {
    expect(withUniqueKeys(["x", "y"], (item) => item).map((entry) => entry.key)).toEqual([
      "x",
      "y",
    ]);
  });
});
