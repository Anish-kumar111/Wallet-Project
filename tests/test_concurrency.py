"""
Race-condition tests: fire the same operation from several threads at once,
each on its own database connection, and check the row locks keep the
result correct. Uses TransactionTestCase so commits are real.
"""

import threading
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TransactionTestCase
from django.utils import timezone

from consultations import billing, services
from wallet import services as wallet_services
from wallet.models import Wallet, WalletTransaction

User = get_user_model()

THREADS = 8


def run_concurrently(fn):
    barrier = threading.Barrier(THREADS)
    errors = []

    def worker():
        try:
            barrier.wait()
            fn()
        except Exception as exc:  # noqa: BLE001 - collected and asserted on below
            errors.append(exc)
        finally:
            connection.close()

    threads = [threading.Thread(target=worker) for _ in range(THREADS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return errors


class ConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="x", role=User.Role.USER)
        self.provider = User.objects.create_user(
            username="dr_bob", password="x", role=User.Role.PROVIDER, per_minute_rate=Decimal("50")
        )

    def balance(self):
        return Wallet.objects.get(user=self.user).balance

    def test_parallel_duplicate_callbacks_credit_once(self):
        recharge = wallet_services.initiate_recharge(self.user, Decimal("500"))

        errors = run_concurrently(lambda: wallet_services.process_recharge_callback(recharge.reference, "success"))

        self.assertEqual(errors, [])
        self.assertEqual(self.balance(), Decimal("500.00"))
        self.assertEqual(WalletTransaction.objects.filter(type="credit").count(), 1)

    def test_parallel_billing_charges_each_minute_once(self):
        wallet_services.credit(self.user, Decimal("500"), reference="fund")
        t0 = timezone.now()
        session = services.create_session(self.user)
        services.assign_session(session.pk, self.user, self.provider.pk)
        services.accept_session(session.pk, self.provider)
        services.start_session(session.pk, self.user, now=t0)

        errors = run_concurrently(lambda: billing.bill_session(session.pk, now=t0 + timedelta(minutes=3)))

        self.assertEqual(errors, [])
        self.assertEqual(self.balance(), Decimal("350.00"))
        self.assertEqual(WalletTransaction.objects.filter(type="debit").count(), 3)
