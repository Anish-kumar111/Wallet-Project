from decimal import Decimal

from rest_framework import serializers

from .models import RechargeTransaction, Wallet, WalletTransaction


class WalletSerializer(serializers.ModelSerializer):
    class Meta:
        model = Wallet
        fields = ["balance", "updated_at"]


class WalletTransactionSerializer(serializers.ModelSerializer):
    class Meta:
        model = WalletTransaction
        fields = ["id", "type", "amount", "balance_after", "reference", "description", "created_at"]


class RechargeTransactionSerializer(serializers.ModelSerializer):
    class Meta:
        model = RechargeTransaction
        fields = ["id", "amount", "reference", "status", "created_at", "updated_at"]


class RechargeInitiateSerializer(serializers.Serializer):
    amount = serializers.DecimalField(
        max_digits=12, decimal_places=2, min_value=Decimal("1.00"), max_value=Decimal("100000.00")
    )


class RechargeCallbackSerializer(serializers.Serializer):
    reference = serializers.CharField(max_length=64)
    status = serializers.ChoiceField(choices=[RechargeTransaction.Status.SUCCESS, RechargeTransaction.Status.FAILED])
