"""
The six scenarios required by the assignment, exercised end-to-end through
the HTTP API.

Elapsed time is simulated by moving the session's `started_at` back
("rewinding" the clock) rather than mocking time, so the real billing code
path runs with the real clock.
"""

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from consultations.models import ConsultationSession
from wallet.models import Wallet, WalletTransaction

User = get_user_model()

RATE = "50.00"
WEBHOOK_HEADERS = {"HTTP_X_WEBHOOK_SECRET": "dev-webhook-secret"}


class RequiredScenarios(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="x", role=User.Role.USER)
        self.provider = User.objects.create_user(
            username="dr_bob", password="x", role=User.Role.PROVIDER, per_minute_rate=Decimal(RATE)
        )

    # --- API helpers ---------------------------------------------------
    def as_(self, actor):
        self.client.force_authenticate(actor)
        return self.client

    def recharge(self, amount):
        ref = self.as_(self.user).post(reverse("recharges"), {"amount": amount}, format="json").data["reference"]
        return ref, self.callback(ref)

    def callback(self, ref):
        self.client.force_authenticate(None)
        return self.client.post(
            reverse("recharge-callback"), {"reference": ref, "status": "success"}, format="json", **WEBHOOK_HEADERS
        )

    def action(self, actor, sid, name, data=None):
        return self.as_(actor).post(reverse(f"session-{name}", args=[sid]), data or {}, format="json")

    def accepted_session(self):
        sid = self.as_(self.user).post(reverse("session-list")).data["id"]
        self.assertEqual(self.action(self.user, sid, "assign", {"provider_id": self.provider.id}).status_code, 200)
        self.assertEqual(self.action(self.provider, sid, "accept").status_code, 200)
        return sid

    def rewind(self, sid, delta: timedelta):
        session = ConsultationSession.objects.get(pk=sid)
        session.started_at -= delta
        session.save(update_fields=["started_at"])

    def balance(self):
        return Wallet.objects.get(user=self.user).balance

    # --- 1 ---------------------------------------------------------------
    def test_1_insufficient_balance_cannot_start(self):
        self.recharge("249.00")  # < 5 x 50
        sid = self.accepted_session()

        res = self.action(self.user, sid, "start")

        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(res.data["error"], "insufficient_balance")
        self.assertEqual(ConsultationSession.objects.get(pk=sid).status, "accepted_by_provider")
        self.assertEqual(self.balance(), Decimal("249.00"))

    # --- 2 ---------------------------------------------------------------
    def test_2_happy_path(self):
        _, cb = self.recharge("500.00")
        self.assertEqual(cb.status_code, 200)
        self.assertEqual(self.balance(), Decimal("500.00"))

        sid = self.accepted_session()
        res = self.action(self.user, sid, "start")
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data["status"], "in_progress")

        self.rewind(sid, timedelta(minutes=2))
        res = self.action(self.user, sid, "bill")
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data["billed_minutes"], 2)
        self.assertEqual(self.balance(), Decimal("400.00"))

        res = self.action(self.user, sid, "end")
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data["status"], "completed")
        self.assertEqual(res.data["ended_by"], "user")
        self.assertEqual(res.data["billed_minutes"], 2)
        self.assertEqual(res.data["billed_amount"], "100.00")
        self.assertGreaterEqual(res.data["duration_seconds"], 120)

        self.assertEqual(self.balance(), Decimal("400.00"))  # 500 - 2 x 50
        self.assertEqual(WalletTransaction.objects.filter(type="debit").count(), 2)

    # --- 3 ---------------------------------------------------------------
    def test_3_balance_runs_out_during_session(self):
        self.recharge("290.00")
        sid = self.accepted_session()
        self.assertEqual(self.action(self.user, sid, "start").status_code, 200)

        # After 5 minutes (250 charged) only 40 is left: the next minute (50) can't be paid.
        self.rewind(sid, timedelta(minutes=5))
        res = self.action(self.user, sid, "bill")

        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data["status"], "completed")
        self.assertEqual(res.data["ended_by"], "system")
        self.assertEqual(res.data["end_reason"], "Insufficient Balance")
        self.assertEqual(res.data["billed_minutes"], 5)
        self.assertIsNotNone(res.data["duration_seconds"])
        self.assertEqual(self.balance(), Decimal("40.00"))
        self.assertGreaterEqual(self.balance(), 0)

        # Nothing more can be charged once the system has ended the session.
        res = self.action(self.user, sid, "bill")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(self.balance(), Decimal("40.00"))

    # --- 4 ---------------------------------------------------------------
    def test_4_invalid_transition_returns_400(self):
        sid = self.accepted_session()

        res = self.action(self.user, sid, "end")  # not started yet

        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(res.data["error"], "invalid_transition")
        self.assertIn("Cannot 'end' a session that is 'accepted_by_provider'", res.data["detail"])
        self.assertEqual(ConsultationSession.objects.get(pk=sid).status, "accepted_by_provider")

    # --- 5 ---------------------------------------------------------------
    def test_5_duplicate_recharge_callback_credits_once(self):
        ref, first = self.recharge("500.00")
        second = self.callback(ref)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertTrue(first.data["applied"])
        self.assertFalse(second.data["applied"])
        self.assertEqual(self.balance(), Decimal("500.00"))
        self.assertEqual(WalletTransaction.objects.filter(type="credit").count(), 1)

    # --- 6 ---------------------------------------------------------------
    def test_6_duplicate_billing_does_not_double_charge(self):
        self.recharge("500.00")
        sid = self.accepted_session()
        self.action(self.user, sid, "start")
        self.rewind(sid, timedelta(minutes=2))

        responses = [self.action(self.user, sid, "bill") for _ in range(3)]
        responses.append(self.action(self.provider, sid, "bill"))

        for res in responses:
            self.assertEqual(res.status_code, 200, res.data)
            self.assertEqual(res.data["billed_minutes"], 2)
        self.assertEqual(self.balance(), Decimal("400.00"))
        self.assertEqual(WalletTransaction.objects.filter(type="debit").count(), 2)
