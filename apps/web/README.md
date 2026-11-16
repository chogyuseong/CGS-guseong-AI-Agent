# apps/web (마이에이전트 화면)

Next.js 16(App Router) + TypeScript(strict) + Tailwind CSS 4 + shadcn/ui + TanStack Query.
브라우저는 같은 출처의 `/api/*`만 부르고, Next.js의 rewrites가 이를 FastAPI(`http://localhost:8000/api/*`)로 넘긴다.
그래서 CORS 설정이 없다.

아래 명령은 모두 Windows PowerShell 5.1에서 `apps/web` 폴더 기준으로 그대로 실행된다.

```powershell
cd apps\web
pnpm install
```

## 개발 서버

```powershell
pnpm dev
```

- PC에서는 `http://localhost:3000`을 연다. 로그인하려면 API 서버(`apps/api`)가 `localhost:8000`에 떠 있어야 한다.
- `pnpm dev`는 `0.0.0.0:3000`으로 열리므로 같은 와이파이의 다른 기기에서도 접속할 수 있다.

## 폰에서 접속하기 (같은 와이파이)

1. PC의 사설 IP 주소를 찾는다.

   ```powershell
   ipconfig
   ```

   "무선 LAN 어댑터 Wi-Fi"(유선이면 "이더넷 어댑터")의 `IPv4 주소`를 본다. 예: `192.168.0.12`.
   `vEthernet (WSL ...)` 같은 가상 어댑터의 주소는 폰에서 닿지 않으니 쓰지 않는다.
   폰과 PC가 같은 공유기에 붙어 있어야 한다.

2. API 서버의 `APP_ORIGIN`을 폰이 여는 주소로 바꾼다. 로그인 요청의 `Origin` 헤더가 이 값과 다르면 서버가 403으로 막는다.
   루트 `.env`에서:

   ```
   APP_ORIGIN=http://192.168.0.12:3000
   ```

   바꾼 뒤 API 서버를 다시 시작한다. 이 값을 바꾸면 PC의 `http://localhost:3000`에서는 로그인이 막힌다
   (PC에서도 `http://192.168.0.12:3000`으로 열면 된다).

3. `pnpm dev`를 실행하고 폰 브라우저에서 `http://192.168.0.12:3000`을 연다.
   - 처음 실행할 때 Windows 방화벽 창이 뜨면 "개인 네트워크"에서 Node.js를 허용한다.
   - 개발 서버는 이 PC의 사설 IP를 실행할 때마다 읽어 `allowedDevOrigins`(개발용 교차 출처 허용 목록)에 넣는다.
     IP가 바뀌면 `pnpm dev`만 다시 실행하면 된다(`next.config.ts`의 `getLanAddresses`).

## 웹 푸시

설정 탭의 '알림 켜기'는 https(또는 `http://localhost`)에서만 된다. 같은 와이파이의 `http://192.168…`로 연 폰에서는
'지원 안 함'으로 보인다. 폰 확인 절차와 터널(`DEV_TUNNEL_HOST`)은 `docs/PUSH-CHECK.md`를 본다.

## 검사 명령

| 명령 | 하는 일 |
| --- | --- |
| `pnpm lint` | Biome 린트 + 포맷 검사 (WASM판, 아래 참고) |
| `pnpm format` | Biome 포맷을 파일에 적용한 뒤 린트 검사 |
| `pnpm typecheck` | `next typegen`으로 경로 타입을 만든 뒤 `tsc --noEmit` |
| `pnpm test` | Vitest 컴포넌트 테스트 |
| `pnpm e2e` | Playwright 화면 흐름 테스트 (360x740 모바일) |

### Biome를 WASM판으로 돌리는 이유

Biome CLI(`biome.exe`)는 서명이 없어 이 PC의 Smart App Control이 실행을 막는다.
그래서 `pnpm lint`는 같은 Biome 엔진의 WebAssembly판(`@biomejs/wasm-nodejs`)을 공식 JS API(`@biomejs/js-api`)로
불러 `biome.json` 설정대로 검사한다(`scripts/biome-check.mjs`).

- CLI가 실행되는 PC(CI 등)에서는 `pnpm lint:cli`(`biome check`)를 써도 된다.
- 한계: WASM판은 import 정렬(assist) 검사를 하지 않고, CSS의 Tailwind 문법 설정을 반영하지 못해 `.css`는 검사하지 않는다.
- 검사 대상에서 뺀 것: `components/ui`(shadcn이 만든 코드), `lib/api/schema.d.ts`(자동 생성).

### Playwright

- 이 PC에서는 Playwright가 내려받는 Chromium도 서명이 없어 막힌다. 그래서 설치된 Google Chrome을 `channel: "chrome"`으로 쓴다.
  Chrome이 설치되어 있어야 한다.
- 실행 전에 API 서버가 `localhost:8000`에 떠 있어야 한다. 준비 단계(`e2e/global-setup.ts`)가
  `/api/health`를 확인하고, `apps/api`에서 아래 명령으로 테스트 전용 계정(`e2e@example.com`)을 만든다.

  ```powershell
  uv run python -m app.cli create-user --email e2e@example.com --password <e2e/test-account.ts의 값>
  ```

- Next 개발 서버는 Playwright가 직접 띄우고 끝나면 끈다(이미 떠 있으면 그것을 쓴다).

## API 타입 생성

FastAPI의 OpenAPI 문서에서 TypeScript 타입을 만든다. 손으로 타입을 쓰지 않는다.

```powershell
pnpm gen:api
```

- `http://localhost:8000/api/openapi.json`을 읽어 `lib/api/schema.d.ts`를 만든다. 이 파일은 직접 고치지 않는다.
- 호출은 `lib/api/client.ts`의 `api`(openapi-fetch)로 한다. 예: `api.GET("/api/auth/me")`.
- API를 바꾸면 같은 커밋에서 `pnpm gen:api`를 다시 실행한다.

## 폴더 구조

```
app/
  layout.tsx            루트 틀(Django의 base.html). TanStack Query Provider를 감싼다.
  page.tsx              "/" → /chat 이동
  login/page.tsx        로그인 화면
  (tabs)/layout.tsx     로그인 확인(AuthGuard) + 하단 탭
  (tabs)/chat|tasks|automation|memory|settings/page.tsx
  (tabs)/connections/page.tsx  외부 서비스 연결(Google). 설정 탭에서 들어간다
  manifest.ts           PWA 설정(/manifest.webmanifest)
components/             BottomTabs, ChatInput, AuthGuard, LoginForm, EmptyScreen, ui/(shadcn)
  PendingTask.tsx       답변 전 task 진행 표시(폴링: 진행 중 1초, 승인 대기 3초, 끝나면 멈춤)
  PushSettings.tsx      설정 탭의 알림 영역('알림 켜기'/끄기, 상태 표시)
  ServiceWorkerRegistrar.tsx  앱을 열 때 public/sw.js 등록
  GoogleConnection.tsx  연결 화면의 Google 영역(상태, 연결 링크, 해제 확인)
  CallbackNotice.tsx    Google에서 돌아왔을 때(?google=...)의 안내, 쿼리 정리
  ExternalContent.tsx   타임라인의 외부 콘텐츠 결과(텍스트로만, http·https 링크만)
  MemoryList.tsx        기억 탭(목록, 원래 메시지 보기, 한 건 삭제, 전체 삭제 확인)
lib/api/                schema.d.ts(자동 생성), client.ts
lib/auth.ts             /api/auth/me, /api/auth/login 훅
lib/chat.ts             대화·메시지·task 훅, 진행 단계(taskPhase)
lib/approvals.ts        승인·거절 호출과 오류 문구
lib/connections.ts      연결 상태·해제 호출, 콜백 결과 코드별 안내
lib/memories.ts         기억 목록·삭제 호출과 오류 문구, 날짜 표시
lib/stepOutput.ts       단계 출력에서 외부 콘텐츠 읽기, 안전한 링크 판정
lib/toolText.ts         도구 칸 이름·시각을 사람이 읽는 글자로
lib/push.ts             웹 푸시 상태 판정, 구독·해지(브라우저·서버를 인자로 받아 테스트 가능)
lib/speech.ts           음성 입력(브라우저 SpeechRecognition, ko-KR). 결과는 입력창에만, 서버로 보내지 않음
public/sw.js            서비스 워커: 푸시 알림 표시, 알림 클릭 시 이동(캐싱·오프라인 없음)
public/icons/           PWA 아이콘 192·512 (0단계는 자리만 잡은 단색 그림)
e2e/                    Playwright
scripts/biome-check.mjs Biome WASM 실행기
```
