"""아직 구현 전인 탭 화면을 공통으로 그리는 도우미. 해당 단계에서 실제 화면으로 바뀐다."""

from django.shortcuts import render


def render_placeholder(request, *, active_tab, title, stage):
    context = {"active_tab": active_tab, "title": title, "stage": stage}
    return render(request, "placeholder.html", context)
