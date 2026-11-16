"""
백그라운드 워커 실행: python manage.py run_worker

웹 서버(runserver)와 별도 터미널에서 띄운다. Celery와 Redis 없이 DB를 큐로 쓴다.
Ctrl+C나 종료 신호(SIGTERM)를 받으면 지금 실행 중인 Task를 끝까지 처리한 뒤 멈춘다.
"""

import signal
import threading
import time

from django.core.management.base import BaseCommand
from django.db import close_old_connections, connection

from agent import worker

POLL_SECONDS = 2  # queued Task를 찾는 간격
MAINTENANCE_SECONDS = 60  # 승인 만료·멈춘 Task 회수 간격


class Command(BaseCommand):
    help = "queued 상태의 Task를 하나씩 실행하는 백그라운드 워커"

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="한 바퀴만 돌고 끝낸다(점검용)")

    def handle(self, *args, once=False, **options):
        stop = threading.Event()
        self._listen_for_stop(stop)
        if connection.vendor == "sqlite":
            self.stdout.write("SQLite 사용 중: 워커는 하나만 띄우세요.")
        self.stdout.write("워커 시작. 멈추려면 Ctrl+C.")
        last_maintenance = float("-inf")
        while not stop.is_set():
            close_old_connections()  # 오래 켜 두는 프로세스라 끊긴 DB 연결을 정리한다
            if time.monotonic() - last_maintenance >= MAINTENANCE_SECONDS:
                self._log_maintenance(worker.run_maintenance())
                last_maintenance = time.monotonic()
            task_id = worker.run_once()
            if task_id:
                self.stdout.write(f"Task {task_id} 처리 끝")
            if once:
                break
            stop.wait(POLL_SECONDS)  # 기다리는 중에 종료 신호가 오면 바로 깬다
        self.stdout.write("워커 종료.")

    def _listen_for_stop(self, stop):
        def request_stop(signum, frame):
            self.stdout.write("종료 신호를 받았습니다. 진행 중인 작업을 마치고 멈춥니다.")
            stop.set()

        for name in ("SIGINT", "SIGTERM"):
            if hasattr(signal, name):
                signal.signal(getattr(signal, name), request_stop)

    def _log_maintenance(self, result):
        if any(result.values()):
            self.stdout.write(f"정리: {result}")
