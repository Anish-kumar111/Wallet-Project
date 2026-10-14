from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models import Q


class User(AbstractUser):
    class Role(models.TextChoices):
        USER = "USER", "User"
        PROVIDER = "PROVIDER", "Provider"

    role = models.CharField(max_length=16, choices=Role.choices, default=Role.USER)
    # Provider's current price. Copied onto a session when it starts, so later
    # changes never affect a session that is already running.
    per_minute_rate = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(per_minute_rate__isnull=True) | Q(per_minute_rate__gt=0),
                name="user_per_minute_rate_positive",
            ),
        ]

    @property
    def is_consumer(self) -> bool:
        return self.role == self.Role.USER

    @property
    def is_provider(self) -> bool:
        return self.role == self.Role.PROVIDER

    def __str__(self):
        return f"{self.username} ({self.role})"
