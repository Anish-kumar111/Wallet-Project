"""
Per-minute billing.

Model: minutes are billed in arrears. Minute N (N = 1, 2, ...) becomes
billable once N full minutes have elapsed since `started_at`. A billing run
charges every billable minute not yet charged, one debit per minute, each
with the ledger reference "session:<id>:minute:<N>".

Why a minute can never be charged twice:
  1. The session row is locked for the whole run, so runs are serialised.
  2. Only minutes after `billed_minutes` are charged, and the counter is
     saved in the same transaction as the debits.
  3. The ledger reference is unique in the database.

Insufficient balance: after charging what is owed, the wallet must still be
able to cover the *next* minute. If a charge fails, or the next minute cannot
be covered, the system ends the session ("Insufficient Balance"). The wallet
never goes negative because `debit()` refuses rather than overdrawing.
"""

import math
from datetime import datetime

from django.db import transaction
from django.utils import timezone

from config.exceptions import DomainError
from wallet.services import InsufficientBalance, debit, lock_wallet

from .models import ConsultationSession
from .state_machine import transition

Status = ConsultationSession.Status
EndedBy = ConsultationSession.EndedBy

INSUFFICIENT_BALANCE_REASON = "Insufficient Balance"


class SessionNotInProgress(DomainError):
    code = "session_not_in_progress"


def billable_minutes(session: ConsultationSession, now: datetime) -> int:
    """Number of full minutes elapsed since the session started."""
    elapsed = (now - session.started_at).total_seconds()
    return max(0, math.floor(elapsed / 60))


def charge_elapsed_minutes(session: ConsultationSession, now: datetime) -> bool:
    """
    Charge every billable minute not yet charged. Mutates `session` (caller
    saves). Returns False if a minute could not be paid for.
    Caller must hold the session row lock.
    """
    for minute in range(session.billed_minutes + 1, billable_minutes(session, now) + 1):
        try:
            debit(
                session.user,
                session.per_minute_cost,
                reference=f"session:{session.pk}:minute:{minute}",
                description=f"Session #{session.pk} minute {minute}",
            )
        except InsufficientBalance:
            return False
        session.billed_minutes = minute
        session.billed_amount += session.per_minute_cost
    return True


def complete(session: ConsultationSession, now: datetime, ended_by: str, reason: str) -> None:
    """Move an in-progress session to completed and persist its talk time. Caller saves."""
    transition(session, "end")
    session.ended_at = now
    session.ended_by = ended_by
    session.end_reason = reason
    session.duration_seconds = max(0, int((now - session.started_at).total_seconds()))


@transaction.atomic
def bill_session(session_id: int, now: datetime | None = None) -> ConsultationSession:
    session = ConsultationSession.objects.select_for_update().get(pk=session_id)
    if session.status != Status.IN_PROGRESS:
        raise SessionNotInProgress(f"Only in-progress sessions can be billed; this session is '{session.status}'.")

    now = now or timezone.now()
    all_paid = charge_elapsed_minutes(session, now)
    can_cover_next_minute = all_paid and lock_wallet(session.user).balance >= session.per_minute_cost

    if not can_cover_next_minute:
        complete(session, now, EndedBy.SYSTEM, INSUFFICIENT_BALANCE_REASON)

    session.save()
    return session


def bill_active_sessions(now: datetime | None = None) -> list[ConsultationSession]:
    """Bill every in-progress session. Each session is billed in its own transaction."""
    billed = []
    for session_id in ConsultationSession.objects.filter(status=Status.IN_PROGRESS).values_list("pk", flat=True):
        try:
            billed.append(bill_session(session_id, now))
        except SessionNotInProgress:
            continue  # ended between the query and the lock
    return billed
