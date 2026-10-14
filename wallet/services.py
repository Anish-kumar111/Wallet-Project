"""
Wallet service layer: the only place that changes wallet balances.

Every balance change runs inside a transaction, holds a row lock on the
wallet (SELECT ... FOR UPDATE) and writes a ledger entry with a unique
reference. Concurrent writers are therefore serialised and the same
business event can never be applied twice.
"""

from decimal import Decimal

from django.db import transaction
from rest_framework import status

from config.exceptions import DomainError

from .models import RechargeTransaction, Wallet, WalletTransaction


class InsufficientBalance(DomainError):
    code = "insufficient_balance"


class RechargeNotFound(DomainError):
    code = "recharge_not_found"
    status_code = status.HTTP_404_NOT_FOUND


class RechargeAlreadyFinalised(DomainError):
    code = "recharge_already_finalised"
    status_code = status.HTTP_409_CONFLICT


def _validate_amount(amount: Decimal) -> None:
    if amount <= 0:
        raise ValueError("Amount must be positive.")


def lock_wallet(user) -> Wallet:
    """Return the user's wallet with a row lock. Must be called inside a transaction."""
    return Wallet.objects.select_for_update().get(user=user)


@transaction.atomic
def credit(user, amount: Decimal, reference: str, description: str = "") -> WalletTransaction:
    _validate_amount(amount)
    wallet = lock_wallet(user)
    wallet.balance += amount
    wallet.save(update_fields=["balance", "updated_at"])
    return WalletTransaction.objects.create(
        wallet=wallet,
        type=WalletTransaction.Type.CREDIT,
        amount=amount,
        balance_after=wallet.balance,
        reference=reference,
        description=description,
    )


@transaction.atomic
def debit(user, amount: Decimal, reference: str, description: str = "") -> WalletTransaction:
    _validate_amount(amount)
    wallet = lock_wallet(user)
    if wallet.balance < amount:
        raise InsufficientBalance(f"Wallet balance {wallet.balance} is less than the required amount {amount}.")
    wallet.balance -= amount
    wallet.save(update_fields=["balance", "updated_at"])
    return WalletTransaction.objects.create(
        wallet=wallet,
        type=WalletTransaction.Type.DEBIT,
        amount=amount,
        balance_after=wallet.balance,
        reference=reference,
        description=description,
    )


def initiate_recharge(user, amount: Decimal) -> RechargeTransaction:
    _validate_amount(amount)
    return RechargeTransaction.objects.create(user=user, amount=amount)


@transaction.atomic
def process_recharge_callback(reference: str, new_status: str) -> tuple[RechargeTransaction, bool]:
    """
    Apply a gateway callback. Idempotent: returns (recharge, applied) where
    `applied` is False when this callback was a duplicate and nothing changed.

    The recharge row is locked first, so two identical callbacks arriving at
    the same time are serialised; the second sees a non-pending status and
    becomes a no-op. The unique ledger reference is a second safety net.
    """
    if new_status not in (RechargeTransaction.Status.SUCCESS, RechargeTransaction.Status.FAILED):
        raise ValueError("Callback status must be 'success' or 'failed'.")

    try:
        recharge = RechargeTransaction.objects.select_for_update().get(reference=reference)
    except RechargeTransaction.DoesNotExist:
        raise RechargeNotFound(f"No recharge found with reference '{reference}'.")

    if recharge.status != RechargeTransaction.Status.PENDING:
        if recharge.status == new_status:
            return recharge, False  # duplicate delivery of the same outcome
        raise RechargeAlreadyFinalised(
            f"Recharge '{reference}' is already '{recharge.status}' and cannot become '{new_status}'."
        )

    if new_status == RechargeTransaction.Status.SUCCESS:
        credit(
            recharge.user,
            recharge.amount,
            reference=f"recharge:{recharge.reference}",
            description="Wallet recharge",
        )

    recharge.status = new_status
    recharge.save(update_fields=["status", "updated_at"])
    return recharge, True
