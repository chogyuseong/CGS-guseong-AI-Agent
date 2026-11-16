"""
에이전트 실행 기록.

Task(요청 한 건) 1:N Step(도구 호출 한 번) 1:1 Approval / GuardCheck 로 나눈다.
한 요청이 여러 도구를 부르고, 그중 외부 행동만 승인이 필요하기 때문에
승인 상태를 Step이 아니라 별도 테이블에 두어 "누가 언제 승인했는지"를 따로 남긴다.
"""

from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone

from config.base_models import TimeStampedModel


class RiskLevel(models.TextChoices):
    """도구 위험 등급. FORBIDDEN은 도구 자체를 만들지 않으므로 여기에 없다."""

    READ = "READ", "조회"
    INTERNAL = "INTERNAL", "앱 내부 변경"
    EXTERNAL = "EXTERNAL", "외부 행동(승인 필요)"


class Task(TimeStampedModel):
    """사용자 요청 한 건의 실행 단위와 상태."""

    class Origin(models.TextChoices):
        CHAT = "chat", "채팅"
        SCHEDULE = "schedule", "예약 작업"
        WATCH = "watch", "조건 감시"

    class Status(models.TextChoices):
        QUEUED = "queued", "대기"
        RUNNING = "running", "실행 중"
        WAITING_APPROVAL = "waiting_approval", "승인 대기"
        DONE = "done", "완료"
        FAILED = "failed", "실패"
        CANCELLED = "cancelled", "취소"
        BUDGET_EXCEEDED = "budget_exceeded", "한도 초과"

    conversation = models.ForeignKey("chat.Conversation", on_delete=models.CASCADE, related_name="tasks")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="tasks")
    # 이 Task를 시작한 사용자 메시지. 기억(Memory)의 출처 검증에 쓴다.
    trigger_message = models.OneToOneField(
        "chat.Message", null=True, blank=True, on_delete=models.SET_NULL, related_name="triggered_task"
    )
    origin = models.CharField(max_length=20, choices=Origin.choices, default=Origin.CHAT)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.QUEUED)
    step_count = models.PositiveIntegerField(default=0)
    total_tokens = models.PositiveIntegerField(default=0)
    final_answer = models.TextField(blank=True)
    error = models.TextField(blank=True)
    # 워커가 잡은 시각. 오래 멈춘 Task를 회수할 때 기준이 된다.
    locked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        # 워커는 "가장 오래된 queued Task"를 자주 찾는다.
        indexes = [models.Index(fields=["status", "created_at"])]

    def __str__(self):
        return f"Task {self.pk} ({self.status})"


class Step(TimeStampedModel):
    """도구 호출 한 번의 입력, 출력, 등급, 소요 시간."""

    class Status(models.TextChoices):
        PENDING = "pending", "대기"
        WAITING_APPROVAL = "waiting_approval", "승인 대기"
        RUNNING = "running", "실행 중"
        DONE = "done", "완료"
        FAILED = "failed", "실패"
        REJECTED = "rejected", "거절됨"

    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="steps")
    order = models.PositiveIntegerField()
    tool_name = models.CharField(max_length=100)
    # LLM이 붙인 호출 id. tool_result를 돌려줄 때 짝을 맞추는 데 쓴다.
    tool_use_id = models.CharField(max_length=100, blank=True)
    # registry에서 복사해 저장한다. 나중에 등급이 바뀌어도 당시 기록은 남는다.
    # LLM이 등록되지 않은 도구를 부른 기록은 등급이 없으므로 비워 둔다.
    risk_level = models.CharField(max_length=20, choices=RiskLevel.choices, blank=True)
    input_json = models.JSONField(default=dict)
    output_json = models.JSONField(null=True, blank=True)
    ok = models.BooleanField(null=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    duration_ms = models.PositiveIntegerField(null=True, blank=True)
    label = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["task_id", "order"]
        constraints = [models.UniqueConstraint(fields=["task", "order"], name="unique_step_order_per_task")]

    def __str__(self):
        return f"Step {self.order}: {self.tool_name}"


def default_approval_expiry():
    return timezone.now() + timedelta(minutes=30)


class Approval(TimeStampedModel):
    """외부 행동 한 건에 대한 사용자 승인. 이 레코드가 approved가 아니면 실행 자체가 불가능하다."""

    class Status(models.TextChoices):
        PENDING = "pending", "승인 대기"
        APPROVED = "approved", "승인"
        REJECTED = "rejected", "거절"
        EXPIRED = "expired", "만료"

    step = models.OneToOneField(Step, on_delete=models.CASCADE, related_name="approval")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    # 승인 카드에 보여 준 preview 문장. 사용자가 무엇을 보고 승인했는지 남긴다.
    summary = models.TextField()
    decided_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(default=default_approval_expiry)

    def __str__(self):
        return f"Approval {self.pk} ({self.status})"


class GuardCheck(TimeStampedModel):
    """감시 에이전트의 판정. 경고만 보탤 뿐 승인을 대신하지 않는다."""

    class Verdict(models.TextChoices):
        MATCH = "match", "요청과 일치"
        MISMATCH = "mismatch", "요청과 다름"
        UNSURE = "unsure", "확인 필요"

    step = models.OneToOneField(Step, on_delete=models.CASCADE, related_name="guard_check")
    verdict = models.CharField(max_length=20, choices=Verdict.choices)
    reason = models.TextField(blank=True)
    model = models.CharField(max_length=100)

    def __str__(self):
        return f"GuardCheck {self.pk} ({self.verdict})"


class UsageLog(TimeStampedModel):
    """모델 호출 한 번의 토큰과 비용. 하루 비용 상한 계산의 근거다."""

    class Purpose(models.TextChoices):
        AGENT = "agent", "에이전트"
        GUARD = "guard", "감시 에이전트"
        WATCH = "watch", "조건 감시"

    # 조건 감시처럼 Task 없이 부르는 경우가 있어 비울 수 있다.
    task = models.ForeignKey(
        Task, null=True, blank=True, on_delete=models.SET_NULL, related_name="usage_logs"
    )
    purpose = models.CharField(max_length=20, choices=Purpose.choices)
    model = models.CharField(max_length=100)
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    cost_usd = models.DecimalField(max_digits=10, decimal_places=6, default=0)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.model} {self.cost_usd}$"
