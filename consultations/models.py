from django.conf import settings
from django.db import models
from django.db.models import Q


class ConsultationSession(models.Model):
    class Status(models.TextChoices):
        UNASSIGNED = "unassigned", "Unassigned"
        ASSIGNED = "assigned", "Assigned"
        ACCEPTED_BY_PROVIDER = "accepted_by_provider", "Accepted by provider"
        IN_PROGRESS = "in_progress", "In progress"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    class EndedBy(models.TextChoices):
        USER = "user", "User"
        PROVIDER = "provider", "Provider"
        SYSTEM = "system", "System"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="consultation_sessions"
    )
    provider = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="provider_sessions",
        null=True,
        blank=True,
    )
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.UNASSIGNED)

    # Snapshot of the provider's rate taken at start; never changes afterwards.
    per_minute_cost = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)

    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    ended_by = models.CharField(max_length=16, choices=EndedBy.choices, null=True, blank=True)
    end_reason = models.CharField(max_length=255, blank=True)

    # Billing progress. Minute N is charged exactly once: billing only ever
    # charges minutes after `billed_minutes`, under a row lock.
    billed_minutes = models.PositiveIntegerField(default=0)
    billed_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    # Actual talk time, persisted when the session completes.
    duration_seconds = models.PositiveIntegerField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            # A user can have at most one in-progress session (DB-level guarantee).
            models.UniqueConstraint(
                fields=["user"],
                condition=Q(status="in_progress"),
                name="one_in_progress_session_per_user",
            ),
            models.CheckConstraint(condition=Q(billed_amount__gte=0), name="session_billed_amount_non_negative"),
        ]

    def __str__(self):
        return f"Session #{self.pk} [{self.status}]"
