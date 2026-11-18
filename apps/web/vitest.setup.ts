// toBeInTheDocument() 같은 화면용 검사 함수를 추가하고, 테스트마다 그린 화면을 지운다.
// vitest.config.mts의 setupFiles가 모든 *.test.ts(x) 전에 이 파일을 실행한다(환경은 jsdom).
// 고칠 때 주의: 여기에 전역 가짜(fetch, localStorage 등)를 넣으면 모든 테스트 파일에 퍼진다(지금은 각 테스트가 직접 둔다).
// cleanup을 빼면 앞 테스트가 그린 화면이 남아 getByRole 등이 여러 개를 찾아 실패한다.
import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

afterEach(() => {
  cleanup();
});
