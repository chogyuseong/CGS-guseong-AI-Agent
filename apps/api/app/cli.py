"""관리 명령. Django의 manage.py createsuperuser에 해당한다.

사용법 (apps/api 폴더에서):
    uv run python -m app.cli create-user --email me@example.com --password 비밀번호
    uv run python -m app.cli create-user --email me@example.com        # 비밀번호를 프롬프트로 묻는다

    uv run python -m app.cli send-test-push --email me@example.com    # 그 계정의 기기들에 시험 푸시

이미 있는 이메일이면 비밀번호를 바꾸지 않고 "이미 있음"으로 정상 종료(0)한다.
웹의 e2e 준비 단계가 이 명령을 반복 실행하기 때문이다.

send-test-push는 폰에서 푸시 수신을 확인하는 용도다(docs/PUSH-CHECK.md). 본문은 notify와 같은 고정 문구다.

진입점: 루트 package.json의 `pnpm api:create-user`, 웹 e2e 준비(apps/web/e2e/global-setup.ts가 create-user 실행).
근거: docs/DECISIONS.md 2026-11-17(send-test-push는 관리 명령이고 API가 아니다).

고칠 때 주의
- 비밀번호는 화면·로그에 찍지 않는다(--password를 빼면 getpass로 화면에 보이지 않게 묻는다).
- 명령 이름·인자를 바꾸면 AGENTS.md "실행", docs/PUSH-CHECK.md, package.json, e2e global-setup을 같이 고친다.
- 이 파일은 서버와 따로 엔진을 만들고 끝나면 닫는다(app.state를 쓰지 않는다). 비동기 실행은 run_async로만
  (Windows SelectorEventLoop, app/event_loop.py).
"""

import argparse
import getpass
import logging
import sys
import uuid

from app.config import get_settings
from app.db import create_engine_for, create_session_factory
from app.event_loop import run_async
from app.push import PushKind, PushNotifier, push_enabled
from app.security import BCRYPT_MAX_BYTES
from app.users import create_user_if_missing, find_user_by_email, normalize_email


def build_parser() -> argparse.ArgumentParser:
    """명령 두 개(create-user, send-test-push)의 인자 해석기. --kind 선택지는 PushKind에서 만든다."""
    parser = argparse.ArgumentParser(prog="python -m app.cli", description="마이에이전트 관리 명령")
    commands = parser.add_subparsers(dest="command", required=True)
    create_user = commands.add_parser("create-user", help="로그인 계정을 만든다")
    create_user.add_argument("--email", required=True)
    create_user.add_argument("--password", help="생략하면 프롬프트로 묻는다")
    test_push = commands.add_parser("send-test-push", help="계정의 알림 구독 기기에 고정 문구 푸시를 보낸다")
    test_push.add_argument("--email", required=True)
    test_push.add_argument("--kind", choices=[k.value for k in PushKind], default=PushKind.APPROVAL_NEEDED.value)
    return parser


def password_problem(password: str) -> str | None:
    """비밀번호에 문제가 있으면 이유를, 없으면 None을 돌려준다.

    bcrypt 한도(72바이트)를 미리 검사해 hash_password의 PasswordTooLongError가 traceback으로 보이지 않게 한다.
    """
    if not password:
        return "비밀번호가 비어 있습니다."
    if len(password.encode("utf-8")) > BCRYPT_MAX_BYTES:
        return f"비밀번호는 UTF-8 기준 {BCRYPT_MAX_BYTES}바이트 이하여야 합니다."
    return None


def prompt_password() -> str:
    """화면에 보이지 않게 두 번 묻는다. 다르면 SystemExit(종료 코드 1, 계정을 만들지 않는다)."""
    first = getpass.getpass("비밀번호: ")
    second = getpass.getpass("비밀번호 확인: ")
    if first != second:
        raise SystemExit("오류: 두 비밀번호가 다릅니다.")
    return first


async def create_user_command(email: str, password: str | None) -> int:
    """계정을 만든다. 종료 코드: 0(만듦·이미 있음), 1(비밀번호 문제). 커밋은 create_user_if_missing이 한다."""
    engine = create_engine_for(get_settings().database_url)
    try:
        async with create_session_factory(engine)() as session:
            # 이미 있으면 비밀번호를 묻지도 않고 끝낸다.
            if await find_user_by_email(session, email) is not None:
                print(f"이미 있음: {normalize_email(email)} (비밀번호는 바꾸지 않았습니다)")
                return 0
            password = password if password is not None else prompt_password()
            if problem := password_problem(password):
                print(f"오류: {problem}", file=sys.stderr)
                return 1
            user = await create_user_if_missing(session, email, password)
    finally:
        await engine.dispose()
    if user is None:
        print(f"이미 있음: {normalize_email(email)} (비밀번호는 바꾸지 않았습니다)")
    else:
        print(f"계정을 만들었습니다: {user.email} (id={user.id})")
    return 0


async def send_test_push_command(email: str, kind: PushKind) -> int:
    """그 계정의 구독 기기 전부에 고정 문구 푸시를 실제로 보낸다(외부 호출: 브라우저 푸시 서버).

    종료 코드: 0(보냄, 0대여도), 1(VAPID 설정 없음·계정 없음). notification_key에 무작위 값을 붙여
    같은 명령을 다시 실행해도 중복 방지(notifications)에 걸리지 않게 한다. 기록은 notifications에 남는다.
    """
    logging.basicConfig(level=logging.WARNING)  # 발송 실패 경고(구독 id와 오류 종류만)를 화면에 보인다
    settings = get_settings()
    if not push_enabled(settings):
        print("오류: .env의 VAPID_PUBLIC_KEY, VAPID_PRIVATE_KEY, VAPID_SUBJECT가 모두 있어야 합니다.", file=sys.stderr)
        return 1
    engine = create_engine_for(settings.database_url)
    try:
        factory = create_session_factory(engine)
        async with factory() as session:
            user = await find_user_by_email(session, email)
        if user is None:
            print(f"오류: 계정이 없습니다: {normalize_email(email)}", file=sys.stderr)
            return 1
        sent = await PushNotifier(factory, settings).notify(user.id, kind, "/chat", f"test:{uuid.uuid4()}")
    finally:
        await engine.dispose()
    print(f"보낸 기기 수: {sent} (0이면 화면에서 '알림 켜기'를 먼저 하세요. 실패 이유는 위 경고 로그 참고)")
    return 0


def main(argv: list[str] | None = None) -> int:
    """종료 코드를 돌려준다. 이메일은 '@'만 가볍게 검사한다(관리자가 직접 치는 값이다)."""
    args = build_parser().parse_args(argv)
    if "@" not in args.email:
        print("오류: 이메일 형식이 아닙니다.", file=sys.stderr)
        return 1
    if args.command == "send-test-push":
        return run_async(send_test_push_command(args.email, PushKind(args.kind)))
    return run_async(create_user_command(args.email, args.password))


if __name__ == "__main__":
    sys.exit(main())
