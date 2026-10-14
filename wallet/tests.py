from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from . import services
from .models import RechargeTransaction, Wallet, WalletTransaction

User = get_user_model()

WEBHOOK_HEADERS = {"HTTP_X_WEBHOOK_SECRET": "dev-webhook-secret"}


def make_user(username="alice"):
    return User.objects.create_user(username=username, password="x", role=User.Role.USER)


class WalletServiceTests(APITestCase):
    def setUp(self):
        self.user = make_user()

    def balance(self):
        return Wallet.objects.get(user=self.user).balance

    def test_wallet_created_for_user_but_not_provider(self):
        provider = User.objects.create_user(username="p", password="x", role=User.Role.PROVIDER, per_minute_rate=10)
        self.assertEqual(self.balance(), Decimal("0.00"))
        self.assertFalse(Wallet.objects.filter(user=provider).exists())

    def test_credit_and_debit_write_ledger(self):
        services.credit(self.user, Decimal("100"), reference="t:1")
        services.debit(self.user, Decimal("30"), reference="t:2")

        self.assertEqual(self.balance(), Decimal("70.00"))
        entries = list(WalletTransaction.objects.order_by("id").values_list("type", "amount", "balance_after"))
        self.assertEqual(entries, [("credit", Decimal("100.00"), Decimal("100.00")), ("debit", Decimal("30.00"), Decimal("70.00"))])

    def test_debit_more_than_balance_is_rejected_and_balance_unchanged(self):
        services.credit(self.user, Decimal("40"), reference="t:1")
        with self.assertRaises(services.InsufficientBalance):
            services.debit(self.user, Decimal("50"), reference="t:2")
        self.assertEqual(self.balance(), Decimal("40.00"))
        self.assertFalse(WalletTransaction.objects.filter(reference="t:2").exists())

    def test_same_reference_cannot_be_applied_twice(self):
        services.credit(self.user, Decimal("10"), reference="dup")
        with self.assertRaises(IntegrityError):
            services.credit(self.user, Decimal("10"), reference="dup")
        self.assertEqual(self.balance(), Decimal("10.00"))

    def test_database_rejects_negative_balance(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Wallet.objects.filter(user=self.user).update(balance=Decimal("-1"))


class WalletApiTests(APITestCase):
    def setUp(self):
        self.user = make_user()
        self.client.force_authenticate(self.user)

    def test_view_balance(self):
        services.credit(self.user, Decimal("25.50"), reference="t:1")
        res = self.client.get(reverse("wallet"))
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["balance"], "25.50")

    def test_provider_has_no_wallet_endpoint(self):
        provider = User.objects.create_user(username="p", password="x", role=User.Role.PROVIDER, per_minute_rate=10)
        self.client.force_authenticate(provider)
        self.assertEqual(self.client.get(reverse("wallet")).status_code, status.HTTP_403_FORBIDDEN)


class RechargeFlowTests(APITestCase):
    def setUp(self):
        self.user = make_user()
        self.client.force_authenticate(self.user)

    def initiate(self, amount="500.00"):
        res = self.client.post(reverse("recharges"), {"amount": amount}, format="json")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)
        self.assertEqual(res.data["status"], "pending")
        return res.data["reference"]

    def callback(self, reference, cb_status="success", headers=WEBHOOK_HEADERS):
        self.client.force_authenticate(None)  # gateway is not a logged-in user
        return self.client.post(
            reverse("recharge-callback"), {"reference": reference, "status": cb_status}, format="json", **headers
        )

    def balance(self):
        return Wallet.objects.get(user=self.user).balance

    def test_initiate_does_not_credit(self):
        self.initiate()
        self.assertEqual(self.balance(), Decimal("0.00"))

    def test_successful_callback_credits_wallet(self):
        ref = self.initiate("500.00")
        res = self.callback(ref)
        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        self.assertTrue(res.data["applied"])
        self.assertEqual(res.data["status"], "success")
        self.assertEqual(self.balance(), Decimal("500.00"))

    def test_duplicate_success_callback_credits_only_once(self):
        """Required scenario 5."""
        ref = self.initiate("500.00")

        first = self.callback(ref)
        second = self.callback(ref)
        third = self.callback(ref)

        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertEqual(third.status_code, status.HTTP_200_OK)
        self.assertTrue(first.data["applied"])
        self.assertFalse(second.data["applied"])
        self.assertFalse(third.data["applied"])

        self.assertEqual(self.balance(), Decimal("500.00"))
        self.assertEqual(WalletTransaction.objects.filter(reference=f"recharge:{ref}").count(), 1)

    def test_failed_callback_does_not_credit(self):
        ref = self.initiate()
        res = self.callback(ref, "failed")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(RechargeTransaction.objects.get(reference=ref).status, "failed")
        self.assertEqual(self.balance(), Decimal("0.00"))

    def test_conflicting_callback_after_final_state_is_rejected(self):
        ref = self.initiate()
        self.callback(ref, "failed")
        res = self.callback(ref, "success")
        self.assertEqual(res.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(res.data["error"], "recharge_already_finalised")
        self.assertEqual(self.balance(), Decimal("0.00"))

    def test_unknown_reference_returns_404(self):
        res = self.callback("RCH-DOESNOTEXIST")
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_callback_requires_webhook_secret(self):
        ref = self.initiate()
        res = self.callback(ref, headers={"HTTP_X_WEBHOOK_SECRET": "wrong"})
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(self.balance(), Decimal("0.00"))

    def test_invalid_recharge_amount_rejected(self):
        for amount in ["0", "-10", "abc"]:
            res = self.client.post(reverse("recharges"), {"amount": amount}, format="json")
            self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST, amount)
