# 웹 푸시 확인 (3단계)

서버 쪽(API)은 준비됐다. 화면 쪽(`public/sw.js`, 설정 화면 '알림 켜기', 매니페스트)은 화면 작업자가 이 문서에 덧붙인다.
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

**Smart App Control**: 위 도구는 실행 파일(exe)이라 이 PC에서 막힐 수 있다("An Application Control policy has blocked this file").
막히면 끄거나 우회하지 말고 다른 도구를 쓴다. Node로 도는 도구(예: `npx localtunnel --port 3000`)는 exe를 받지 않는다.

터널을 쓸 때 바꿀 것:
1. `.env`의 `APP_ORIGIN`을 터널 주소(예: `'https://abc-def.trycloudflare.com'`)로 바꾸고 API 서버를 다시 켠다.
   상태를 바꾸는 요청은 Origin이 이 값과 같아야 하고, https라 세션 쿠키에 Secure가 붙는다. 확인이 끝나면 되돌린다.
2. Next.js 개발 서버가 터널 주소를 거부하면 화면 작업자가 `next.config`의 `allowedDevOrigins`에 그 호스트를 넣는다(화면 쪽 확인 필요).

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
