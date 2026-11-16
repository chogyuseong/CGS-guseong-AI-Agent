"""
웹 푸시용 VAPID 키 쌍 만들기: python manage.py generate_vapid_keys --write-env

  --write-env  .env의 VAPID_PUBLIC_KEY, VAPID_PRIVATE_KEY가 비어 있으면 채운다(값은 화면에 출력하지 않는다).
  옵션 없이   두 키를 화면에 출력한다. 배포 서버의 환경변수에 직접 넣을 때 쓴다.

키 형식: 공개키는 브라우저 applicationServerKey에 넣는 65바이트 점(uncompressed point),
개인키는 32바이트 raw 값. 둘 다 base64url(패딩 없음)로 인코딩한다. pywebpush가 그대로 읽는다.
"""

import base64
import re

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


def generate_keys():
    private_key = ec.generate_private_key(ec.SECP256R1())
    private_raw = private_key.private_numbers().private_value.to_bytes(32, "big")
    public_raw = private_key.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    return _b64url(public_raw), _b64url(private_raw)


def _b64url(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


class Command(BaseCommand):
    help = "웹 푸시용 VAPID 키 쌍을 만든다"

    def add_arguments(self, parser):
        parser.add_argument("--write-env", action="store_true", help=".env의 빈 VAPID 키를 채운다")

    def handle(self, *args, write_env=False, **options):
        public_key, private_key = generate_keys()
        if not write_env:
            self.stdout.write(f"VAPID_PUBLIC_KEY={public_key}\nVAPID_PRIVATE_KEY={private_key}")
            return
        env_path = settings.BASE_DIR / ".env"
        if not env_path.exists():
            raise CommandError(".env 파일이 없습니다. .env.example을 복사해 먼저 만드세요.")
        text = env_path.read_text(encoding="utf-8")
        for name in ("VAPID_PUBLIC_KEY", "VAPID_PRIVATE_KEY"):
            if re.search(rf"^{name}=\S", text, flags=re.MULTILINE):
                raise CommandError(f"{name}가 이미 채워져 있습니다. 덮어쓰지 않습니다.")
        text = re.sub(r"^VAPID_PUBLIC_KEY=.*$", f"VAPID_PUBLIC_KEY={public_key}", text, flags=re.MULTILINE)
        text = re.sub(r"^VAPID_PRIVATE_KEY=.*$", f"VAPID_PRIVATE_KEY={private_key}", text, flags=re.MULTILINE)
        env_path.write_text(text, encoding="utf-8")
        self.stdout.write(".env에 VAPID 키를 저장했습니다. VAPID_SUBJECT(mailto: 주소)도 채워 주세요.")
