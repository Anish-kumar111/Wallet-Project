from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from wallet import services as wallet_services
from wallet.models import Wallet

from .models import ConsultationSession

User = get_user_model()
Status = ConsultationSession.Status

RATE = Decimal("50.00")


class SessionTestBase(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="x", role=User.Role.USER)
        self.provider = User.objects.create_user(
            username="dr_bob", password="x", role=User.Role.PROVIDER, per_minute_rate=RATE
        )

    # --- helpers -------------------------------------------------------
    def fund(self, amount):
        wallet_services.credit(self.user, Decimal(amount), reference=f"test-fund:{amount}:{Wallet.objects.count()}")

    def act(self, actor, session_id, action, data=None):
        self.client.force_authenticate(actor)
        return self.client.post(reverse(f"session-{action}", args=[session_id]), data or {}, format="json")

    def create_session(self):
        self.client.force_authenticate(self.user)
        res = self.client.post(reverse("session-list"))
        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)
        return res.data["id"]

    def accepted_session(self):
        sid = self.create_session()
        self.assertEqual(self.act(self.user, sid, "assign", {"provider_id": self.provider.id}).status_code, 200)
        self.assertEqual(self.act(self.provider, sid, "accept").status_code, 200)
        return sid

    def status_of(self, sid):
        return ConsultationSession.objects.get(pk=sid).status


class LifecycleTests(SessionTestBase):
    def test_full_lifecycle_user_ends(self):
        self.fund("250.00")
        sid = self.create_session()
        self.assertEqual(self.status_of(sid), Status.UNASSIGNED)

        res = self.act(self.user, sid, "assign", {"provider_id": self.provider.id})
        self.assertEqual(res.data["status"], Status.ASSIGNED)
        self.assertEqual(res.data["provider"], self.provider.id)

        res = self.act(self.provider, sid, "accept")
        self.assertEqual(res.data["status"], Status.ACCEPTED_BY_PROVIDER)

        res = self.act(self.user, sid, "start")
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data["status"], Status.IN_PROGRESS)
        self.assertEqual(res.data["per_minute_cost"], "50.00")
        self.assertIsNotNone(res.data["started_at"])

        res = self.act(self.user, sid, "end")
        self.assertEqual(res.data["status"], Status.COMPLETED)
        self.assertEqual(res.data["ended_by"], "user")
        self.assertIsNotNone(res.data["ended_at"])

    def test_provider_can_end_session(self):
        self.fund("250.00")
        sid = self.accepted_session()
        self.act(self.user, sid, "start")
        res = self.act(self.provider, sid, "end")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["ended_by"], "provider")

    def test_cancel_before_start(self):
        sid = self.accepted_session()
        res = self.act(self.provider, sid, "cancel")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["status"], Status.CANCELLED)
        self.assertEqual(res.data["ended_by"], "provider")


class InvalidTransitionTests(SessionTestBase):
    def assert_invalid(self, res):
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST, res.data)
        self.assertEqual(res.data["error"], "invalid_transition")
        self.assertIn("Cannot", res.data["detail"])

    def test_end_before_start_returns_400(self):
        """Required scenario 4."""
        sid = self.accepted_session()
        res = self.act(self.user, sid, "end")
        self.assert_invalid(res)
        self.assertIn("accepted_by_provider", res.data["detail"])
        self.assertEqual(self.status_of(sid), Status.ACCEPTED_BY_PROVIDER)

    def test_start_before_accept_returns_400(self):
        self.fund("1000.00")
        sid = self.create_session()
        self.act(self.user, sid, "assign", {"provider_id": self.provider.id})
        self.assert_invalid(self.act(self.user, sid, "start"))

    def test_accept_twice_returns_400(self):
        sid = self.accepted_session()
        self.assert_invalid(self.act(self.provider, sid, "accept"))

    def test_actions_on_completed_session_return_400(self):
        self.fund("250.00")
        sid = self.accepted_session()
        self.act(self.user, sid, "start")
        self.act(self.user, sid, "end")
        for action in ["start", "end", "cancel"]:
            self.assert_invalid(self.act(self.user, sid, action))

    def test_cannot_cancel_in_progress_session(self):
        self.fund("250.00")
        sid = self.accepted_session()
        self.act(self.user, sid, "start")
        self.assert_invalid(self.act(self.user, sid, "cancel"))


class PermissionTests(SessionTestBase):
    def test_provider_cannot_create_session(self):
        self.client.force_authenticate(self.provider)
        self.assertEqual(self.client.post(reverse("session-list")).status_code, status.HTTP_403_FORBIDDEN)

    def test_only_assigned_provider_can_accept(self):
        other = User.objects.create_user(username="dr_x", password="x", role=User.Role.PROVIDER, per_minute_rate=10)
        sid = self.create_session()
        self.act(self.user, sid, "assign", {"provider_id": self.provider.id})
        # Unrelated provider can't even see the session.
        self.assertEqual(self.act(other, sid, "accept").status_code, status.HTTP_404_NOT_FOUND)
        # The session's own user is not allowed to accept.
        res = self.act(self.user, sid, "accept")
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(res.data["error"], "not_allowed")

    def test_provider_cannot_start_session(self):
        self.fund("250.00")
        sid = self.accepted_session()
        self.assertEqual(self.act(self.provider, sid, "start").status_code, status.HTTP_403_FORBIDDEN)

    def test_assign_requires_a_valid_provider(self):
        other_user = User.objects.create_user(username="bob", password="x", role=User.Role.USER)
        sid = self.create_session()
        for bad_id in [other_user.id, 999999]:
            res = self.act(self.user, sid, "assign", {"provider_id": bad_id})
            self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
            self.assertEqual(res.data["error"], "invalid_provider")
        self.assertEqual(self.status_of(sid), Status.UNASSIGNED)

    def test_sessions_list_scoped_to_participants(self):
        sid = self.accepted_session()
        stranger = User.objects.create_user(username="eve", password="x", role=User.Role.USER)
        for actor, expected in [(self.user, [sid]), (self.provider, [sid]), (stranger, [])]:
            self.client.force_authenticate(actor)
            ids = [s["id"] for s in self.client.get(reverse("session-list")).data]
            self.assertEqual(ids, expected)


class StartRulesTests(SessionTestBase):
    def test_insufficient_balance_cannot_start(self):
        """Required scenario 1: less than 5 minutes of balance (5 x 50 = 250)."""
        self.fund("249.99")
        sid = self.accepted_session()

        res = self.act(self.user, sid, "start")

        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(res.data["error"], "insufficient_balance")
        self.assertIn("250.00", res.data["detail"])
        session = ConsultationSession.objects.get(pk=sid)
        self.assertEqual(session.status, Status.ACCEPTED_BY_PROVIDER)
        self.assertIsNone(session.started_at)
        self.assertEqual(Wallet.objects.get(user=self.user).balance, Decimal("249.99"))

    def test_exactly_five_minutes_of_balance_can_start(self):
        self.fund("250.00")
        sid = self.accepted_session()
        self.assertEqual(self.act(self.user, sid, "start").status_code, 200)

    def test_only_one_in_progress_session_per_user(self):
        provider2 = User.objects.create_user(username="dr_c", password="x", role=User.Role.PROVIDER, per_minute_rate=RATE)
        self.fund("1000.00")
        first = self.accepted_session()
        self.assertEqual(self.act(self.user, first, "start").status_code, 200)

        second = self.create_session()
        self.act(self.user, second, "assign", {"provider_id": provider2.id})
        self.act(provider2, second, "accept")
        res = self.act(self.user, second, "start")

        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(res.data["error"], "active_session_exists")
        self.assertEqual(self.status_of(second), Status.ACCEPTED_BY_PROVIDER)

        # After ending the first one, the second may start.
        self.act(self.user, first, "end")
        self.assertEqual(self.act(self.user, second, "start").status_code, 200)

    def test_db_enforces_one_in_progress_session(self):
        ConsultationSession.objects.create(user=self.user, status=Status.IN_PROGRESS)
        with self.assertRaises(IntegrityError), transaction.atomic():
            ConsultationSession.objects.create(user=self.user, status=Status.IN_PROGRESS)

    def test_per_minute_cost_fixed_at_start(self):
        self.fund("250.00")
        sid = self.accepted_session()
        self.act(self.user, sid, "start")

        self.provider.per_minute_rate = Decimal("99.00")
        self.provider.save()

        self.assertEqual(ConsultationSession.objects.get(pk=sid).per_minute_cost, RATE)

    def test_rate_change_before_start_is_used(self):
        self.provider.per_minute_rate = Decimal("10.00")
        self.provider.save()
        self.fund("50.00")
        sid = self.accepted_session()
        res = self.act(self.user, sid, "start")
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data["per_minute_cost"], "10.00")
