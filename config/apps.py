from django.contrib.staticfiles.apps import StaticFilesConfig


class ProjectStaticFilesConfig(StaticFilesConfig):
    """정적 파일 모으기(collectstatic)에서 Tailwind 원본(static/src/)을 뺀다.

    static/src/input.css는 Tailwind CLI의 입력 파일이라 '@import "tailwindcss"'가 들어 있다.
    배포용 매니페스트 저장소는 CSS 안의 @import 경로를 실제 파일로 찾으려 하므로,
    이 파일이 섞이면 collectstatic 전체가 실패한다(2026-11-16 확인). 브라우저에 보낼 파일도 아니다.
    배포에 쓰는 결과물은 빌드해서 커밋한 static/css/app.css다.
    """

    ignore_patterns = [*StaticFilesConfig.ignore_patterns, "src"]
