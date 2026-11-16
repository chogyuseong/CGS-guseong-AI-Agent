"""
마이에이전트 설정.

비밀값과 환경마다 다른 값은 전부 환경변수(.env)에서 읽는다. 코드에는 기본값만 둔다.
(절대 원칙 8: 키와 토큰은 .env와 암호화된 DB 필드에만 둔다)
"""

import os
from decimal import Decimal
from pathlib import Path

import dj_database_url
from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# .env가 있으면 읽는다. 이미 설정된 환경변수는 덮어쓰지 않는다.
load_dotenv(BASE_DIR / ".env")


def env_str(name, default=""):
    return os.environ.get(name, default)


def env_bool(name, default=False):
    return os.environ.get(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


def env_list(name, default=""):
    return [item.strip() for item in os.environ.get(name, default).split(",") if item.strip()]


# ── 기본 ────────────────────────────────────────────────
SECRET_KEY = env_str("SECRET_KEY")
if not SECRET_KEY:
    # 키 없이 조용히 뜨는 것보다 바로 멈추는 편이 안전하다.
    raise ImproperlyConfigured("SECRET_KEY 환경변수가 없습니다. .env.example을 참고해 .env를 만드세요.")

# 기본값을 False로 두어, 설정을 빠뜨린 배포가 디버그 모드로 뜨지 않게 한다.
DEBUG = env_bool("DEBUG", False)
ALLOWED_HOSTS = env_list("ALLOWED_HOSTS", "localhost,127.0.0.1")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    # django.contrib.staticfiles와 같다. Tailwind 원본(static/src/)만 모으지 않는다(config/apps.py).
    "config.apps.ProjectStaticFilesConfig",
    # 프로젝트 앱
    "accounts",
    "chat",
    "agent",
    "memory",
    "automation",
    "notifications",
    "tools",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # 배포에서는 gunicorn이 정적 파일을 내보내지 않는다. WhiteNoise가 대신 내보낸다.
    # 공식 문서대로 SecurityMiddleware 바로 다음에 둔다(HTTPS 리디렉션 등이 먼저 적용되게).
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# ── DB ──────────────────────────────────────────────────


def database_config():
    """개발은 SQLite, 배포는 PostgreSQL. DATABASE_URL 하나만 바꾸면 된다.

    .env에 'DATABASE_URL='처럼 빈 값만 있어도 SQLite를 쓴다.
    SQLite는 URL 대신 경로를 직접 넘긴다(폴더 이름의 공백과 한글이 URL 해석에서 깨지지 않게).
    """
    url = env_str("DATABASE_URL").strip()
    if url:
        return dj_database_url.parse(url, conn_max_age=60)
    return {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "db.sqlite3"}


DATABASES = {"default": database_config()}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# ── 언어와 시간 ─────────────────────────────────────────
# DB에는 UTC로 저장하고, 화면과 에이전트는 서울 시간으로 해석한다.
LANGUAGE_CODE = "ko-kr"
TIME_ZONE = "Asia/Seoul"
USE_I18N = True
USE_TZ = True

# ── 정적 파일 ───────────────────────────────────────────
STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
# 배포에서는 collectstatic이 파일 이름에 내용 해시를 붙이고(app.3f2a....css) 압축본(gzip, br)도 만든다.
# 해시가 붙은 파일은 내용이 바뀌면 주소도 바뀌므로 브라우저가 오래 캐시해도 옛 CSS가 남지 않는다.
# 이 저장소는 collectstatic이 만든 매니페스트가 없으면 DEBUG=False에서 {% static %}이 오류를 낸다.
# 개발(DEBUG=True)은 해시 없는 주소를 쓰므로 괜찮고, 테스트는 settings_test에서 일반 저장소로 바꾼다.
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}
# WhiteNoise의 기본 형식표에 .webmanifest가 없어 application/octet-stream으로 나간다(6.12.0에서 확인).
# PWA 매니페스트의 표준 형식을 직접 알려 준다.
WHITENOISE_MIMETYPES = {".webmanifest": "application/manifest+json"}

# ── 로그인 ──────────────────────────────────────────────
# 회원가입 화면은 만들지 않는다. createsuperuser로 내 계정만 만든다.
LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "chat:home"
LOGOUT_REDIRECT_URL = "accounts:login"

# ── 보안 쿠키 ───────────────────────────────────────────
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
# 개발 서버는 http라서 Secure 쿠키를 쓰면 로그인이 안 된다. 배포(DEBUG=False)에서만 켠다.
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG

# ── HTTPS (배포) ────────────────────────────────────────
# 아래 값은 전부 환경변수로 켠다. 기본은 꺼짐이라 로컬 개발(http)과 테스트의 동작은 그대로다.
#
# 로그인 폼 같은 POST는 CSRF 검사에서 Origin 헤더를 확인한다. 배포 주소(https://...)를 여기 넣어 둔다.
# 반드시 https://로 시작하는 전체 주소다. 예: https://my-agent.up.railway.app
CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS")

# Railway 같은 호스팅은 앞단 프록시가 HTTPS를 풀고 앱에는 http로 넘긴다. 그러면 Django는
# 요청이 https였는지 모른다(Secure 쿠키, HTTPS 리디렉션, CSRF 검사가 엉뚱하게 동작).
# 프록시가 붙여 주는 X-Forwarded-Proto 헤더를 믿게 하는 설정이다.
# 그 헤더를 프록시가 항상 덮어써 주는 환경에서만 켠다.
# 프록시 없이 켜면 누구나 헤더를 꾸며 https인 척할 수 있다.
SECURE_PROXY_SSL_HEADER = (
    ("HTTP_X_FORWARDED_PROTO", "https") if env_bool("SECURE_PROXY_SSL_HEADER", False) else None
)
# http로 온 요청을 https로 돌려보낸다. 프록시 뒤에서는 위 SECURE_PROXY_SSL_HEADER와 함께 켜야 한다.
# 하나만 켜면 앱은 모든 요청을 http로 보고 계속 리디렉션한다(무한 리디렉션).
SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", False)
# HSTS: 브라우저에게 "이 사이트는 앞으로 https로만 접속하라"고 알린다(초 단위, 0이면 끔).
# 한 번 보내면 그 기간 동안 브라우저가 http 접속을 거부하므로, 처음에는 짧게(3600) 시험한 뒤 늘린다.
SECURE_HSTS_SECONDS = int(env_str("SECURE_HSTS_SECONDS", "0"))
SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool("SECURE_HSTS_INCLUDE_SUBDOMAINS", False)

# ── 에이전트 ────────────────────────────────────────────
# 모델 이름은 코드에 박지 않고 여기서만 바꾼다.
AGENT_MODEL_FAST = env_str("AGENT_MODEL_FAST", "claude-haiku-5-5")
AGENT_MODEL_SMART = env_str("AGENT_MODEL_SMART", "claude-sonnet-5-5")
AGENT_MODEL_GUARD = env_str("AGENT_MODEL_GUARD", "claude-haiku-5-5")
ANTHROPIC_API_KEY = env_str("ANTHROPIC_API_KEY")

# 비용 폭주를 막는 상한. 넘으면 Task를 budget_exceeded로 끝낸다.
DAILY_COST_LIMIT_USD = Decimal(env_str("DAILY_COST_LIMIT_USD", "1.0"))
TASK_MAX_STEPS = int(env_str("TASK_MAX_STEPS", "12"))
TASK_MAX_TOKENS = int(env_str("TASK_MAX_TOKENS", "60000"))

# 모델별 가격(100만 토큰당 달러). Anthropic 공식 가격표 기준, 2026-11-16 확인.
# 가격이 None인 모델은 실제 호출이 막힌다(agent/llm.py). 모델을 바꾸면 가격도 함께 넣는다.
# Haiku 5.5는 프롬프트 10만 토큰까지의 가격이다. 이 앱은 Task당 6만 토큰 상한이라 넘지 않는다.
# 캐시 가격: 쓰기(5분 유지)는 입력의 1.25배, 읽기는 Haiku 0.1배, Sonnet 5.5 0.05배.
MODEL_PRICES = {
    "claude-haiku-5-5": {
        "input": Decimal("0.10"),
        "output": Decimal("0.50"),
        "cache_write": Decimal("0.125"),
        "cache_read": Decimal("0.01"),
    },
    "claude-sonnet-5-5": {
        "input": Decimal("2.00"),
        "output": Decimal("10.00"),
        "cache_write": Decimal("2.50"),
        "cache_read": Decimal("0.10"),
    },
}

# ── 외부 API 키 (해당 도구를 만드는 단계에서 발급) ─────
KAKAO_REST_API_KEY = env_str("KAKAO_REST_API_KEY")
NAVER_CLIENT_ID = env_str("NAVER_CLIENT_ID")
NAVER_CLIENT_SECRET = env_str("NAVER_CLIENT_SECRET")
GOOGLE_CLIENT_ID = env_str("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = env_str("GOOGLE_CLIENT_SECRET")
GOOGLE_REDIRECT_URI = env_str("GOOGLE_REDIRECT_URI")
TOKEN_ENCRYPTION_KEY = env_str("TOKEN_ENCRYPTION_KEY")
VAPID_PUBLIC_KEY = env_str("VAPID_PUBLIC_KEY")
VAPID_PRIVATE_KEY = env_str("VAPID_PRIVATE_KEY")
VAPID_SUBJECT = env_str("VAPID_SUBJECT")
