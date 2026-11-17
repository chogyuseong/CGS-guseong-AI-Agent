# 웹 푸시 확인 (3단계)

서버 쪽(API)과 화면 쪽(`public/sw.js`, 설정 화면 '알림 켜기')이 모두 준비됐다. 화면 쪽 확인 절차는 아래 6을 본다.
푸시 본문 규칙은 `docs/SAFETY.md`의 "푸시 본문 규칙"을 본다.

## 1. VAPID 키

웹 푸시는 서버가 VAPID 키(P-256 타원곡선 키 한 쌍)로 서명해 보낸다. `.env`의 세 값이 모두 있어야 푸시가 나간다.

| 변수 | 형식 | 지금 상태 |
| --- | --- | --- |
| `VAPID_PUBLIC_KEY` | base64url, 87자(65바이트 비압축 공개 키). 브라우저 `applicationServerKey`에 들어간다 | Django판에서 가져온 값이 있다. 형식 검사 통과(값은 출력하지 않음) |
| `VAPID_PRIVATE_KEY` | base64url, 43자(32바이트 개인 키) | 같다. 공개 키와 짝이 맞는 것을 확인했다 |
| `VAPID_SUBJECT` | `mailto:본인메일` 또는 `https://주소`. 푸시 서버가 문제를 알릴 연락처 | **비어 있다 → 지금은 푸시가 나가지 않는다** |

### 할 일: VAPID_SUBJECT 채우기

`.env`에 한 줄을 채우고 API 서버를 다시 켠다.

```text
VAPID_SUBJECT='mailto:본인메일@example.com'
```

### 키를 새로 만들어야 할 때 (지금 키가 새거나 잃어버린 경우만)

키를 바꾸면 기존 구독이 모두 무효가 되어 기기마다 '알림 켜기'를 다시 해야 한다. `apps/api`에서 실행하면 두 줄이 화면에 나온다.
화면에 나온 값을 `.env`에 옮겨 적고, 터미널 기록은 지운다(이 값은 비밀이다).

```powershell
cd apps/api
uv run python -c "import base64; from cryptography.hazmat.primitives.asymmetric import ec; from cryptography.hazmat.primitives import serialization as s; k=ec.generate_private_key(ec.SECP256R1()); b=lambda x: base64.urlsafe_b64encode(x).decode().rstrip('='); print('VAPID_PUBLIC_KEY=' + repr(b(k.public_key().public_bytes(s.Encoding.X962, s.PublicFormat.UncompressedPoint)))); print('VAPID_PRIVATE_KEY=' + repr(b(k.private_numbers().private_value.to_bytes(32, 'big'))))"
```

## 2. 서버 API (화면 작업자용)

| 요청 | 응답 |
| --- | --- |
| `GET /api/push/public-key` | `{"enabled": bool, "public_key": str \| null}`. enabled=false면 '알림 켜기'를 막고 "서버 알림 설정이 없습니다" 안내 |
| `POST /api/push/subscribe` 본문 = `subscription.toJSON()` | 204. endpoint가 푸시 서비스 https 주소가 아니면 422 |
| `POST /api/push/unsubscribe` 본문 `{"endpoint"}` | 204 |

푸시 payload(서비스 워커의 `event.data.json()`): `{"title": "마이에이전트", "body": "승인이 필요한 작업이 있습니다", "url": "/chat/7", "kind": "approval_needed"}`.
kind는 `approval_needed`, `task_done`, `task_failed`(3단계부터), `watch_met`, `briefing`(6단계). 알림을 누르면 `url`을 연다.

언제 오나: 승인 필요(항상), 작업 실패·상한 초과(항상), 작업 완료(20초 넘게 걸린 채팅 작업, 예약·감시 작업).

## 3. HTTPS가 필요하다

서비스 워커와 푸시는 "안전한 출처"에서만 된다: `https://…` 또는 `http://localhost`.
PC 브라우저의 `http://localhost:3000`은 되지만, **폰에서 `http://192.168.x.x:3000`(같은 와이파이)으로 열면 푸시를 켤 수 없다.**
폰으로 확인하려면 https 주소가 필요하다.

### 로컬에서 폰으로: 터널링

PC의 3000번을 인터넷의 https 주소로 잠깐 열어 주는 도구를 쓴다. 터널 주소를 아는 사람은 누구나 로그인 화면에 닿으므로
확인이 끝나면 바로 끈다.

| 도구 | 명령 (PC에서) | 비고 |
| --- | --- | --- |
| Cloudflare Tunnel(빠른 터널) | `cloudflared tunnel --url http://localhost:3000` | 계정 없이 `https://…trycloudflare.com` 주소가 나온다. `winget install Cloudflare.cloudflared` |
| ngrok | `ngrok http 3000` | 무료 계정과 토큰 필요 |
| Tailscale Funnel | `tailscale funnel 3000` | Tailscale 계정 필요 |

**교육장 네트워크(2026-11-17 확인)**: cloudflared가 쓰는 7844 포트가 UDP·TCP 모두 막혀 빠른 터널이 붙지 않는다
(`--protocol http2`도 실패). localtunnel은 주소는 나오지만 연결이 자주 끊겨 503이 난다. 이 네트워크에서는 PC를 폰 핫스팟에
연결하거나 다른 네트워크에서 확인한다. cloudflared를 막 설치한 창에서는 명령을 못 찾으니
`& "C:\Program Files (x86)\cloudflared\cloudflared.exe" tunnel --url http://127.0.0.1:3000`처럼 전체 경로로 실행한다.
localtunnel은 `--local-host 127.0.0.1`을 붙인다(화면 서버가 IPv4로만 열려 있어 `localhost`가 ::1로 가면 연결 거부).

**Smart App Control**: 위 도구는 실행 파일(exe)이라 이 PC에서 막힐 수 있다("An Application Control policy has blocked this file").
막히면 끄거나 우회하지 말고 다른 도구를 쓴다. Node로 도는 도구(예: `npx localtunnel --port 3000`)는 exe를 받지 않는다.

터널을 쓸 때 바꿀 것:
1. `.env`의 `APP_ORIGIN`을 터널 주소(예: `'https://abc-def.trycloudflare.com'`)로 바꾸고 API 서버를 다시 켠다.
   상태를 바꾸는 요청은 Origin이 이 값과 같아야 하고, https라 세션 쿠키에 Secure가 붙는다. 확인이 끝나면 되돌린다.
2. Next.js 개발 서버는 처음 보는 출처의 개발용 요청을 막는다. 터널 호스트 이름(https://와 포트 없이)을 환경변수로 주고 화면 서버를 켠다.
   `pnpm dev`(루트)를 쓰면 그 창에서 같은 줄을 먼저 실행한다. 확인이 끝나면 창을 닫으면 사라진다.

   ```powershell
   $env:DEV_TUNNEL_HOST = "abc-def.trycloudflare.com"
   pnpm web
   ```

## 4. 폰에서 받아 보기

### 안드로이드 (Chrome)

1. 폰 Chrome으로 터널 주소를 열고 로그인한다(홈 화면에 추가하지 않아도 되지만, 추가하면 앱처럼 열린다).
2. 설정 탭 → '알림 켜기' → 권한 허용.
3. PC에서 시험 푸시를 보낸다(아래 5).
4. 알림 "마이에이전트 / 승인이 필요한 작업이 있습니다"가 오고, 누르면 채팅 화면이 열린다.
5. 안 오면: 폰 설정 → 앱 → Chrome → 알림이 켜져 있는지, 절전 모드·방해 금지가 아닌지 본다.

### 아이폰 (iOS 16.4 이상, Safari)

아이폰은 **홈 화면에 추가한 앱에서만** 웹 푸시가 된다(Safari 탭에서는 '알림 켜기'가 동작하지 않는다).

1. Safari로 터널 주소를 연다 → 공유 버튼 → '홈 화면에 추가'.
2. 홈 화면의 마이에이전트 아이콘으로 연다(Safari가 아니라 이 아이콘으로) → 로그인.
3. 설정 탭 → '알림 켜기' → 권한 허용(버튼을 눌렀을 때만 권한을 묻는다. iOS는 사용자 동작 없이 묻는 것을 막는다).
4. PC에서 시험 푸시를 보낸다.
5. 안 오면: 아이폰 설정 → 알림 → 마이에이전트가 허용인지, 집중 모드가 아닌지 본다. 터널 주소가 바뀌면 다른 앱으로 취급되므로
   홈 화면 아이콘을 지우고 새 주소로 다시 추가하고 '알림 켜기'를 다시 한다.

## 5. 시험 푸시 보내기

4단계 전에는 외부(EXTERNAL) 도구가 없어 실제 흐름으로 '승인 필요' 푸시를 만들 수 없다. 관리 명령으로 같은 고정 문구를 보낸다.

```powershell
cd apps/api
uv run python -m app.cli send-test-push --email 본인메일@example.com
uv run python -m app.cli send-test-push --email 본인메일@example.com --kind task_done
```

`보낸 기기 수: 1`처럼 나오면 푸시 서버까지 보낸 것이다. 0이면 그 계정으로 '알림 켜기'를 한 기기가 없다.
경고 로그에 오류 종류가 보이면(구독 id만 찍힌다) 그 구독은 다시 켜야 할 수 있다. 만료(404·410)된 구독은 자동으로 지워진다.

실제 흐름 확인(20초 넘는 작업): 할 일 여러 개를 하나씩 추가하는 긴 요청을 보내고 폰 화면을 끈다. 20초가 넘게 걸렸으면
'맡기신 작업이 끝났습니다'가 온다.

## 6. 화면 쪽 확인 (설정 탭의 알림 영역)

화면 쪽 구성: `apps/web/public/sw.js`(푸시 수신·알림 클릭), `components/ServiceWorkerRegistrar.tsx`(앱을 열 때 sw.js 등록),
`components/PushSettings.tsx`(설정 탭의 알림 영역), `lib/push.ts`(상태 판정, 구독·해지). 캐싱·오프라인 기능은 없다.

### 알림 영역의 상태 표시

하단 탭 **설정** → **알림** 카드 오른쪽 위 배지가 지금 상태다.

| 배지 | 뜻 | 화면에 보이는 것 | 할 일 |
| --- | --- | --- | --- |
| 지원 안 함 | https가 아니거나(같은 와이파이 `http://192.168…`) 브라우저가 웹 푸시를 못 함 | https 안내 또는 "이 브라우저에서는…" + 아이폰 '홈 화면에 추가' 안내. 버튼 없음 | 터널 https 주소로 연다. 아이폰은 홈 화면 아이콘으로 연다 |
| 서버에 키 없음 | 서버 `GET /api/push/public-key`가 enabled=false(VAPID 세 값 중 하나라도 빔) | '알림 켜기' 버튼이 회색(눌리지 않음) | 위 1의 `VAPID_SUBJECT`를 채우고 API 서버를 다시 켠다 |
| 권한 거부됨 | 예전에 권한 창에서 '차단'을 누름 | 브라우저 설정에서 허용하라는 안내. 버튼 없음 | 아래 "권한을 거부했을 때" |
| 꺼짐 | 켤 수 있지만 이 기기는 구독하지 않음 | '알림 켜기' 버튼 | 버튼을 누른다 |
| 켜짐 | 권한 허용 + 이 기기 구독이 서버에 저장됨 | '알림 끄기' 버튼 | — |

- 권한 창은 **'알림 켜기'를 눌렀을 때만** 뜬다. 화면을 여는 것만으로는 묻지 않는다.
- 다른 앱(브라우저·폰 설정)에서 권한을 바꾸고 돌아오면 배지가 다시 계산된다(안 바뀌면 설정 탭을 다시 연다).
- 서버 VAPID 키를 바꾸면 이 기기 구독은 '꺼짐'으로 보인다. '알림 켜기'를 다시 누르면 예전 구독을 해지하고 새 키로 구독한다.

### PC에서 먼저 확인 (Chrome, `http://localhost:3000`)

localhost는 https가 아니어도 서비스 워커가 된다. 폰 확인 전에 화면 흐름을 여기서 본다.

1. `pnpm dev`(루트) 또는 API + `pnpm web`을 띄우고 `http://localhost:3000`에서 로그인한다.
2. F12 → **Application** 탭 → **Service workers**: `sw.js`가 `activated and is running`이면 등록된 것이다.
   (Network 탭에서 `/sw.js` 응답 헤더에 `Cache-Control: no-cache, no-store, must-revalidate`가 보인다.)
3. 하단 탭 **설정** → 배지가 '서버에 키 없음'이면 위 1을 먼저 한다. '꺼짐'이면 **알림 켜기** → 권한 창에서 **허용**.
4. 배지가 **켜짐**으로 바뀐다. 실패하면 카드 아래에 빨간 한국어 문구가 나온다
   (예: "푸시 서비스에 등록하지 못했습니다" = 브라우저가 푸시 서버에 닿지 못함. 회사망·확장 프로그램을 확인).
5. 위 5의 `send-test-push`를 보낸다 → PC 오른쪽 아래에 "마이에이전트 / 승인이 필요한 작업이 있습니다" 알림.
6. 알림을 누른다 → 이미 열린 마이에이전트 탭이 앞으로 오고 `/chat/…`으로 이동한다. 탭을 모두 닫은 상태면 새 탭이 열린다.
7. **알림 끄기** → 배지 '꺼짐'. 다시 `send-test-push`를 보내면 `보낸 기기 수: 0`.
8. Application → Service workers의 **Push** 칸에 아무 글자나 넣고 Push를 누르면 기본 문구("새 알림이 있습니다.")로 알림이 뜬다
   (payload가 JSON이 아니어도 깨지지 않는지 보는 것. 누르면 `/chat`).

### 폰에서 (위 4의 절차에 더해 화면에서 볼 것)

1. 터널 https 주소로 로그인 → 하단 탭 **설정**.
   - 아이폰 Safari 탭에서 열면 배지가 **지원 안 함**이고 "'홈 화면에 추가'를 한 뒤…" 안내가 보이는 것이 정상이다.
     홈 화면 아이콘으로 다시 열면 '꺼짐'이 된다.
   - 안드로이드 Chrome은 탭에서 바로 '꺼짐'이다.
2. **알림 켜기** → 권한 허용 → 배지 **켜짐**.
3. 앱을 닫거나(홈으로 나가기) 화면을 끈 뒤 PC에서 `send-test-push`.
4. 알림을 누르면 앱이 열리고 채팅 화면으로 간다.
5. 알림 문구가 고정 문구뿐인지(대화 내용·일정 제목이 없는지) 본다.

### 권한을 거부했을 때

권한 창에서 '차단'을 누르면 배지가 '권한 거부됨'이 되고 앱에서 다시 물을 수 없다(브라우저 규칙).

- PC Chrome: 주소창 왼쪽 아이콘 → 사이트 설정 → 알림 → 허용.
- 안드로이드 Chrome: 주소창 왼쪽 아이콘 → 권한 → 알림 허용(또는 폰 설정 → 앱 → Chrome → 알림).
- 아이폰: 설정 → 알림 → 마이에이전트 → 알림 허용.

허용한 뒤 설정 탭으로 돌아와 **알림 켜기**를 누른다.

### 진행 상황 이어 보기 (즉시 응답 + 폴링)

메시지를 보내면 서버가 바로 답하고, 화면은 답변 자리에서 task를 1초마다 가져와 "곧 시작합니다 → 작업하는 중 → (승인 카드) →
결정을 반영해 이어서 처리하는 중 → 답변" 순서로 보여 준다. 긴 요청을 보낸 뒤 새로 고침하거나 다른 앱에 갔다 와도
같은 자리에 진행 상황이 다시 보여야 한다. 승인 대기 중에는 3초마다 가져온다(다른 기기에서 결정해도 반영).
