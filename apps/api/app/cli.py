"""관리 명령. Django의 manage.py createsuperuser에 해당한다.

사용법 (apps/api 폴더에서):
    uv run python -m app.cli create-user --email me@example.com --password 비밀번호
    uv run python -m app.cli create-user --email me@example.com        # 비밀번호를 프롬프트로 묻는다

    uv run python -m app.cli send-test-push --email me@example.com    # 그 계정의 기기들에 시험 푸시

이미 있는 이메일이면 비밀번호를 바꾸지 않고 "이미 있음"으로 정상 종료(0)한다.
웹의 e2e 준비 단계가 이 명령을 반복 실행하기 때문이다.

send-test-push는 폰에서 푸시 수신을 확인하는 용도다(docs/PUSH-CHECK.md). 본문은 notify와 같은 고정 문구다.
"""

import argparse
import getpass
import logging
import sys

from app.config import get_settings
from app.db import create_engine_for, create_session_factory
from app.event_loop import run_async
from app.push import PushKind, PushNotifier, push_enabled
from app.security import BCRYPT_MAX_BYTES
from app.users import create_user_if_missing, find_user_by_email, normalize_email


def build_parser() -> argparse.ArgumentParser:
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
    """비밀번호에 문제가 있으면 이유를, 없으면 None을 돌려준다."""
    if not password:
        return "비밀번호가 비어 있습니다."
    if len(password.encode("utf-8")) > BCRYPT_MAX_BYTES:
        return f"비밀번호는 UTF-8 기준 {BCRYPT_MAX_BYTES}바이트 이하여야 합니다."
    return None


def prompt_password() -> str:
    first = getpass.getpass("비밀번호: ")
    second = getpass.getpass("비밀번호 확인: ")
    if first != second:
        raise SystemExit("오류: 두 비밀번호가 다릅니다.")
    return first


async def create_user_command(email: str, password: str | None) -> int:
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
        sent = await PushNotifier(factory, settings).notify(user.id, kind, "/chat")
    finally:
        await engine.dispose()
    print(f"보낸 기기 수: {sent} (0이면 화면에서 '알림 켜기'를 먼저 하세요. 실패 이유는 위 경고 로그 참고)")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if "@" not in args.email:
        print("오류: 이메일 형식이 아닙니다.", file=sys.stderr)
        return 1
    if args.command == "send-test-push":
        return run_async(send_test_push_command(args.email, PushKind(args.kind)))
    return run_async(create_user_command(args.email, args.password))


if __name__ == "__main__":
    sys.exit(main())
