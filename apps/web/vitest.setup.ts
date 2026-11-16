// toBeInTheDocument() 같은 화면용 검사 함수를 추가하고, 테스트마다 그린 화면을 지운다.
import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

afterEach(() => {
  cleanup();
});
