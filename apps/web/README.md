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
  manifest.ts           PWA 설정(/manifest.webmanifest)
components/             BottomTabs, ChatInput, AuthGuard, LoginForm, EmptyScreen, ui/(shadcn)
lib/api/                schema.d.ts(자동 생성), client.ts
lib/auth.ts             /api/auth/me, /api/auth/login 훅
public/icons/           PWA 아이콘 192·512 (0단계는 자리만 잡은 단색 그림)
e2e/                    Playwright
scripts/biome-check.mjs Biome WASM 실행기
```
