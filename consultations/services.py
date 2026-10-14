"""
Session service layer. Each operation:

  1. locks the session row (SELECT ... FOR UPDATE) inside a transaction,
  2. checks the actor is allowed to perform it,
  3. checks the state machine allows it,
  4. applies the change.

Lock order is always session -> wallet, so session operations cannot
deadlock with each other.
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from rest_framework import status

from config.exceptions import DomainError
from wallet.services import InsufficientBalance, lock_wallet

from .billing import INSUFFICIENT_BALANCE_REASON, charge_elapsed_minutes, complete
from .models import ConsultationSession
from .state_machine import ensure_can, transition

User = get_user_model()
Status = ConsultationSession.Status
EndedBy = ConsultationSession.EndedBy

# A session may only start if the wallet covers this many minutes up front.
MIN_START_BALANCE_MINUTES = 5


class NotAllowed(DomainError):
    code = "not_allowed"
    status_code = status.HTTP_403_FORBIDDEN


class ActiveSessionExists(DomainError):
    code = "active_session_exists"


class InvalidProvider(DomainError):
    code = "invalid_provider"


def _lock(session_id: int) -> ConsultationSession:
    return ConsultationSession.objects.select_for_update().get(pk=session_id)


def create_session(user) -> ConsultationSession:
    if not user.is_consumer:
        raise NotAllowed("Only users can create sessions.")
    return ConsultationSession.objects.create(user=user)


@transaction.atomic
def assign_session(session_id: int, actor, provider_id: int) -> ConsultationSession:
    session = _lock(session_id)
    if actor != session.user:
        raise NotAllowed("Only the session owner can assign a provider.")
    transition(session, "assign")

    provider = User.objects.filter(
        pk=provider_id, role=User.Role.PROVIDER, is_active=True, per_minute_rate__isnull=False
    ).first()
    if provider is None:
        raise InvalidProvider(f"User {provider_id} is not an active provider with a per-minute rate.")

    session.provider = provider
    session.save(update_fields=["provider", "status", "updated_at"])
    return session


@transaction.atomic
def accept_session(session_id: int, actor) -> ConsultationSession:
    session = _lock(session_id)
    if actor != session.provider:
        raise NotAllowed("Only the assigned provider can accept this session.")
    transition(session, "accept")
    session.save(update_fields=["status", "updated_at"])
    return session


@transaction.atomic
def start_session(session_id: int, actor, now=None) -> ConsultationSession:
    session = _lock(session_id)
    if actor != session.user:
        raise NotAllowed("Only the session owner can start the session.")
    transition(session, "start")

    # Locking the wallet also serialises concurrent starts by the same user,
    # so the "one in-progress session" check below cannot race.
    wallet = lock_wallet(session.user)

    if ConsultationSession.objects.filter(user=session.user, status=Status.IN_PROGRESS).exists():
        raise ActiveSessionExists("You already have a session in progress. End it before starting another.")

    rate: Decimal = session.provider.per_minute_rate
    required = rate * MIN_START_BALANCE_MINUTES
    if wallet.balance < required:
        raise InsufficientBalance(
            f"A minimum balance of {required} ({MIN_START_BALANCE_MINUTES} minutes at {rate}/min) "
            f"is required to start this session. Current balance: {wallet.balance}."
        )

    session.per_minute_cost = rate
    session.started_at = now or timezone.now()
    session.save(update_fields=["status", "per_minute_cost", "started_at", "updated_at"])
    return session


@transaction.atomic
def end_session(session_id: int, actor, now=None) -> ConsultationSession:
    session = _lock(session_id)
    if actor == session.user:
        ended_by = EndedBy.USER
    elif actor == session.provider:
        ended_by = EndedBy.PROVIDER
    else:
        raise NotAllowed("Only the session's user or provider can end it.")
    ensure_can(session, "end")

    # Final billing run: charge every full minute up to now. A trailing
    # partial minute is not charged.
    now = now or timezone.now()
    reason = f"Ended by {ended_by}"
    if not charge_elapsed_minutes(session, now):
        reason += f" (final billing stopped: {INSUFFICIENT_BALANCE_REASON})"

    complete(session, now, ended_by, reason)
    session.save()
    return session


@transaction.atomic
def cancel_session(session_id: int, actor) -> ConsultationSession:
    session = _lock(session_id)
    if actor == session.user:
        ended_by = EndedBy.USER
    elif session.provider is not None and actor == session.provider:
        ended_by = EndedBy.PROVIDER
    else:
        raise NotAllowed("Only the session's user or provider can cancel it.")
    transition(session, "cancel")

    session.ended_at = timezone.now()
    session.ended_by = ended_by
    session.end_reason = f"Cancelled by {ended_by}"
    session.save(update_fields=["status", "ended_at", "ended_by", "end_reason", "updated_at"])
    return session
