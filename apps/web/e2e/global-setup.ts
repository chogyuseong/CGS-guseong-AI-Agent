import { spawnSync } from "node:child_process";
import path from "node:path";
import { E2E_EMAIL, E2E_PASSWORD } from "./test-account";

const API_HEALTH_URL = "http://localhost:8000/api/health";
const API_DIR = path.resolve(__dirname, "../../api");

// API 서버가 떠 있는지 먼저 확인한다. 안 떠 있으면 이유를 분명히 알리고 멈춘다.
async function assertApiRunning(): Promise<void> {
  try {
    const response = await fetch(API_HEALTH_URL);
    if (response.ok) return;
    throw new Error(`상태 코드 ${response.status}`);
  } catch (error) {
    throw new Error(
      `API 서버(${API_HEALTH_URL})에 연결하지 못했습니다. apps/api 서버를 먼저 띄우세요. 원인: ${String(error)}`,
    );
  }
}

// apps/api의 관리 명령으로 테스트 계정을 만든다. 이미 있으면 "이미 있음"으로 끝나므로 그대로 진행한다.
// (Django의 manage.py createsuperuser를 테스트 준비 단계에서 부르는 것과 같다)
function createTestUser(): void {
  const result = spawnSync(
    "uv",
    [
      "run",
      "python",
      "-m",
      "app.cli",
      "create-user",
      "--email",
      E2E_EMAIL,
      "--password",
      E2E_PASSWORD,
    ],
    // PYTHONUTF8=1: 윈도우에서 파이썬 출력이 cp949로 깨지지 않게 UTF-8로 고정한다.
    { cwd: API_DIR, encoding: "utf8", env: { ...process.env, PYTHONUTF8: "1" } },
  );
  const output = `${result.stdout ?? ""}${result.stderr ?? ""}`;
  if (result.status === 0 || output.includes("이미 있음")) return;
  throw new Error(`테스트 계정을 만들지 못했습니다 (종료 코드 ${result.status}):\n${output}`);
}

export default async function globalSetup(): Promise<void> {
  await assertApiRunning();
  createTestUser();
}
