import hmac

from django.conf import settings
from rest_framework import generics, status
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import IsUserRole

from . import services
from .models import RechargeTransaction, Wallet, WalletTransaction
from .serializers import (
    RechargeCallbackSerializer,
    RechargeInitiateSerializer,
    RechargeTransactionSerializer,
    WalletSerializer,
    WalletTransactionSerializer,
)


class WalletView(generics.RetrieveAPIView):
    serializer_class = WalletSerializer
    permission_classes = [IsUserRole]

    def get_object(self):
        return Wallet.objects.get(user=self.request.user)


class WalletTransactionListView(generics.ListAPIView):
    serializer_class = WalletTransactionSerializer
    permission_classes = [IsUserRole]

    def get_queryset(self):
        return WalletTransaction.objects.filter(wallet__user=self.request.user)


class RechargeListCreateView(generics.ListCreateAPIView):
    """GET lists the user's recharges; POST initiates a new pending recharge."""

    serializer_class = RechargeTransactionSerializer
    permission_classes = [IsUserRole]

    def get_queryset(self):
        return RechargeTransaction.objects.filter(user=self.request.user)

    def create(self, request, *args, **kwargs):
        payload = RechargeInitiateSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        recharge = services.initiate_recharge(request.user, payload.validated_data["amount"])
        return Response(RechargeTransactionSerializer(recharge).data, status=status.HTTP_201_CREATED)


class RechargeCallbackView(APIView):
    """
    Mock payment-gateway webhook. Called server-to-server, so it is not
    authenticated with a user JWT; instead the gateway proves itself with a
    shared secret in the X-Webhook-Secret header.

    Safe to call repeatedly: duplicates return 200 with "applied": false.
    """

    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        provided = request.headers.get("X-Webhook-Secret", "")
        if not hmac.compare_digest(provided, settings.RECHARGE_WEBHOOK_SECRET):
            raise PermissionDenied("Invalid webhook secret.")

        payload = RechargeCallbackSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        recharge, applied = services.process_recharge_callback(
            payload.validated_data["reference"], payload.validated_data["status"]
        )
        return Response({**RechargeTransactionSerializer(recharge).data, "applied": applied})
