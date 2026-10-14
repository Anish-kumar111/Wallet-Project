"""
Session lifecycle, declared as data:

    unassigned --assign--> assigned --accept--> accepted_by_provider --start--> in_progress --end--> completed
        \                     |                         |
         \------------------cancel------------------------> cancelled

Every status change goes through `transition()`, so an action that is not
listed for the session's current status can never be applied.
"""

from dataclasses import dataclass

from config.exceptions import DomainError

from .models import ConsultationSession

Status = ConsultationSession.Status


class InvalidTransition(DomainError):
    code = "invalid_transition"


@dataclass(frozen=True)
class Transition:
    sources: frozenset
    target: str


TRANSITIONS: dict[str, Transition] = {
    "assign": Transition(frozenset({Status.UNASSIGNED}), Status.ASSIGNED),
    "accept": Transition(frozenset({Status.ASSIGNED}), Status.ACCEPTED_BY_PROVIDER),
    "start": Transition(frozenset({Status.ACCEPTED_BY_PROVIDER}), Status.IN_PROGRESS),
    "end": Transition(frozenset({Status.IN_PROGRESS}), Status.COMPLETED),
    "cancel": Transition(
        frozenset({Status.UNASSIGNED, Status.ASSIGNED, Status.ACCEPTED_BY_PROVIDER}), Status.CANCELLED
    ),
}


def ensure_can(session: ConsultationSession, action: str) -> Transition:
    """Raise InvalidTransition unless `action` is allowed from the session's current status."""
    rule = TRANSITIONS[action]
    if session.status not in rule.sources:
        allowed = sorted(name for name, t in TRANSITIONS.items() if session.status in t.sources)
        raise InvalidTransition(
            f"Cannot '{action}' a session that is '{session.status}'. "
            f"Allowed actions from this state: {', '.join(allowed) or 'none'}."
        )
    return rule


def transition(session: ConsultationSession, action: str) -> None:
    session.status = ensure_can(session, action).target
