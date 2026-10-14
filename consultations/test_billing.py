"""
Billing tests at the service layer. Time is controlled by passing an explicit
`now`, so results do not depend on how fast the tests run.
"""

from datetime import timedelta
from decimal import Decimal
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from wallet import services as wallet_services
from wallet.models import Wallet, WalletTransaction

from . import billing, services
from .models import ConsultationSession

User = get_user_model()
Status = ConsultationSession.Status

RATE = Decimal("50.00")


def minutes(m, s=0):
    return timedelta(minutes=m, seconds=s)


class BillingTestBase(APITestCase):
    def setUp(self):
        self.t0 = timezone.now().replace(microsecond=0)
        self.user = User.objects.create_user(username="alice", password="x", role=User.Role.USER)
        self.provider = User.objects.create_user(
            username="dr_bob", password="x", role=User.Role.PROVIDER, per_minute_rate=RATE
        )
        self._fund_seq = 0

    def fund(self, amount, user=None):
        self._fund_seq += 1
        wallet_services.credit(user or self.user, Decimal(amount), reference=f"test-fund:{self._fund_seq}")

    def balance(self, user=None):
        return Wallet.objects.get(user=user or self.user).balance

    def start(self, user=None, provider=None):
        user, provider = user or self.user, provider or self.provider
        session = services.create_session(user)
        services.assign_session(session.pk, user, provider.pk)
        services.accept_session(session.pk, provider)
        return services.start_session(session.pk, user, now=self.t0)

    def bill_at(self, session, offset):
        return billing.bill_session(session.pk, now=self.t0 + offset)


class PerMinuteBillingTests(BillingTestBase):
    def test_nothing_charged_before_first_full_minute(self):
        self.fund("500")
        s = self.bill_at(self.start(), minutes(0, 59))
        self.assertEqual(s.billed_minutes, 0)
        self.assertEqual(self.balance(), Decimal("500.00"))

    def test_partial_minute_not_charged(self):
        self.fund("500")
        s = self.bill_at(self.start(), minutes(2, 59))
        self.assertEqual(s.billed_minutes, 2)
        self.assertEqual(s.billed_amount, Decimal("100.00"))
        self.assertEqual(self.balance(), Decimal("400.00"))

    def test_incremental_billing_runs(self):
        self.fund("500")
        s = self.start()
        self.bill_at(s, minutes(1))
        s = self.bill_at(s, minutes(3))
        self.assertEqual(s.billed_minutes, 3)
        self.assertEqual(self.balance(), Decimal("350.00"))
        refs = set(WalletTransaction.objects.filter(type="debit").values_list("reference", flat=True))
        self.assertEqual(refs, {f"session:{s.pk}:minute:{n}" for n in (1, 2, 3)})

    def test_repeated_billing_for_same_minute_charges_once(self):
        self.fund("500")
        s = self.start()
        for offset in [minutes(2), minutes(2), minutes(2, 30), minutes(2, 59)]:
            s = self.bill_at(s, offset)
        self.assertEqual(s.billed_minutes, 2)
        self.assertEqual(self.balance(), Decimal("400.00"))
        self.assertEqual(WalletTransaction.objects.filter(type="debit").count(), 2)

    def test_cost_fixed_even_if_provider_changes_rate_mid_session(self):
        self.fund("500")
        s = self.start()
        self.provider.per_minute_rate = Decimal("80.00")
        self.provider.save()
        s = self.bill_at(s, minutes(2))
        self.assertEqual(s.billed_amount, Decimal("100.00"))

    def test_billing_non_active_session_is_rejected(self):
        self.fund("500")
        s = self.start()
        services.end_session(s.pk, self.user, now=self.t0 + minutes(1))
        with self.assertRaises(billing.SessionNotInProgress):
            self.bill_at(s, minutes(5))
        self.assertEqual(self.balance(), Decimal("450.00"))


class InsufficientBalanceTests(BillingTestBase):
    def test_spec_example_balance_40_cost_50_ends_session(self):
        # 290 - 5 minutes x 50 = 40 left, which cannot cover the next minute.
        self.fund("290")
        s = self.bill_at(self.start(), minutes(5))

        self.assertEqual(s.status, Status.COMPLETED)
        self.assertEqual(s.ended_by, "system")
        self.assertEqual(s.end_reason, "Insufficient Balance")
        self.assertEqual(s.billed_minutes, 5)
        self.assertEqual(s.duration_seconds, 300)
        self.assertEqual(self.balance(), Decimal("40.00"))

    def test_late_billing_run_charges_what_it_can_and_never_goes_negative(self):
        # Billing missed several ticks: 7 minutes owed, only 5 affordable.
        self.fund("250")
        s = self.bill_at(self.start(), minutes(7, 10))

        self.assertEqual(s.status, Status.COMPLETED)
        self.assertEqual(s.ended_by, "system")
        self.assertEqual(s.billed_minutes, 5)
        self.assertEqual(s.billed_amount, Decimal("250.00"))
        self.assertEqual(s.duration_seconds, 430)  # actual talk time is persisted
        self.assertEqual(self.balance(), Decimal("0.00"))

    def test_session_continues_while_next_minute_is_covered(self):
        self.fund("300")
        s = self.bill_at(self.start(), minutes(4))  # 100 left, next minute covered
        self.assertEqual(s.status, Status.IN_PROGRESS)
        self.assertEqual(self.balance(), Decimal("100.00"))

    def test_top_up_during_session_keeps_it_running(self):
        self.fund("250")
        s = self.start()
        s = self.bill_at(s, minutes(4))  # 50 left
        self.fund("100")
        s = self.bill_at(s, minutes(5))  # 100 left
        self.assertEqual(s.status, Status.IN_PROGRESS)
        self.assertEqual(self.balance(), Decimal("100.00"))


class EndSessionBillingTests(BillingTestBase):
    def test_end_charges_full_minutes_only(self):
        self.fund("500")
        s = self.start()
        s = services.end_session(s.pk, self.user, now=self.t0 + minutes(2, 40))
        self.assertEqual(s.billed_minutes, 2)
        self.assertEqual(s.billed_amount, Decimal("100.00"))
        self.assertEqual(s.duration_seconds, 160)
        self.assertEqual(self.balance(), Decimal("400.00"))

    def test_end_after_partial_billing_charges_only_the_remainder(self):
        self.fund("500")
        s = self.start()
        self.bill_at(s, minutes(2))
        s = services.end_session(s.pk, self.provider, now=self.t0 + minutes(3, 5))
        self.assertEqual(s.billed_minutes, 3)
        self.assertEqual(s.ended_by, "provider")
        self.assertEqual(self.balance(), Decimal("350.00"))

    def test_end_when_final_billing_cannot_be_paid(self):
        self.fund("250")
        s = self.start()
        s = services.end_session(s.pk, self.user, now=self.t0 + minutes(6))
        self.assertEqual(s.status, Status.COMPLETED)
        self.assertEqual(s.ended_by, "user")
        self.assertIn("Insufficient Balance", s.end_reason)
        self.assertEqual(s.billed_minutes, 5)
        self.assertEqual(self.balance(), Decimal("0.00"))


class BillAllAndApiTests(BillingTestBase):
    def test_bill_active_sessions_only_touches_in_progress(self):
        other = User.objects.create_user(username="carol", password="x", role=User.Role.USER)
        finished_user = User.objects.create_user(username="dan", password="x", role=User.Role.USER)
        for u in (self.user, other, finished_user):
            self.fund("500", user=u)
        s1 = self.start()
        s2 = self.start(user=other)
        finished = self.start(user=finished_user)
        services.end_session(finished.pk, finished_user, now=self.t0 + minutes(0, 30))

        billed = billing.bill_active_sessions(now=self.t0 + minutes(2))

        self.assertEqual({s.pk for s in billed}, {s1.pk, s2.pk})
        self.assertEqual(self.balance(), Decimal("400.00"))
        self.assertEqual(self.balance(other), Decimal("400.00"))
        self.assertEqual(self.balance(finished_user), Decimal("500.00"))

    def test_management_command(self):
        self.fund("500")
        s = self.start()
        ConsultationSession.objects.filter(pk=s.pk).update(started_at=timezone.now() - minutes(2))
        out = StringIO()
        call_command("bill_active_sessions", stdout=out)
        self.assertIn(f"session {s.pk}: status=in_progress billed_minutes=2", out.getvalue())

    def test_bill_endpoint_scoped_to_participants(self):
        self.fund("500")
        s = self.start()
        stranger = User.objects.create_user(username="eve", password="x", role=User.Role.USER)
        self.client.force_authenticate(stranger)
        res = self.client.post(reverse("session-bill", args=[s.pk]))
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_bill_endpoint_on_completed_session_returns_400(self):
        self.fund("500")
        s = self.start()
        services.end_session(s.pk, self.user)
        self.client.force_authenticate(self.user)
        res = self.client.post(reverse("session-bill", args=[s.pk]))
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(res.data["error"], "session_not_in_progress")
