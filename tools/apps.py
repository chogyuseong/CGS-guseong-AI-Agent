from django.apps import AppConfig


class ToolsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "tools"

    def ready(self):
        # 도구 모듈을 불러오면 registry에 등록된다. 새 도구 파일을 만들면 여기에 한 줄 추가한다.
        from tools import gcal, notes, places, weather, websearch  # noqa: F401
