import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q


class Wallet(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="wallet")
    balance = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            # Last line of defence: the service layer already refuses overdrafts,
            # but the database will also reject any write that goes negative.
            models.CheckConstraint(condition=Q(balance__gte=0), name="wallet_balance_non_negative"),
        ]

    def __str__(self):
        return f"Wallet({self.user.username}: {self.balance})"


class WalletTransaction(models.Model):
    """Append-only ledger of every balance change."""

    class Type(models.TextChoices):
        CREDIT = "credit", "Credit"
        DEBIT = "debit", "Debit"

    wallet = models.ForeignKey(Wallet, on_delete=models.CASCADE, related_name="transactions")
    type = models.CharField(max_length=8, choices=Type.choices)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    balance_after = models.DecimalField(max_digits=12, decimal_places=2)
    # Unique business key (e.g. "recharge:<ref>", "session:<id>:minute:<n>").
    # Guarantees the same credit/debit can never be applied twice.
    reference = models.CharField(max_length=128, unique=True)
    description = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.CheckConstraint(condition=Q(amount__gt=0), name="wallet_txn_amount_positive"),
        ]

    def __str__(self):
        return f"{self.type} {self.amount} ({self.reference})"


def generate_recharge_reference() -> str:
    return f"RCH-{uuid.uuid4().hex[:16].upper()}"


class RechargeTransaction(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SUCCESS = "success", "Success"
        FAILED = "failed", "Failed"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="recharges")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    reference = models.CharField(max_length=64, unique=True, default=generate_recharge_reference, editable=False)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.CheckConstraint(condition=Q(amount__gt=0), name="recharge_amount_positive"),
        ]

    def __str__(self):
        return f"Recharge {self.reference} {self.amount} [{self.status}]"
