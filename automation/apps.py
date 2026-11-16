from django.apps import AppConfig


class AutomationConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "automation"

    def ready(self):
        # 새 계정이 생기면 아침 브리핑 예약 작업을 기본으로 만든다(명세).
        from automation import signals  # noqa: F401
