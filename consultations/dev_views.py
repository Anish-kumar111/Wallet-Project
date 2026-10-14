"""
Development-only helpers for manual testing (e.g. from the React test console).
Only routed when settings.DEBUG is True; see config/urls.py.
"""

from datetime import timedelta

from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import ConsultationSession
from .serializers import ConsultationSessionSerializer


class RewindSerializer(serializers.Serializer):
    minutes = serializers.IntegerField(min_value=1, max_value=120)


class RewindSessionClockView(APIView):
    """Move an in-progress session's started_at back, simulating elapsed time."""

    def post(self, request, pk):
        me = request.user
        session = get_object_or_404(ConsultationSession.objects.filter(Q(user=me) | Q(provider=me)), pk=pk)
        if session.status != ConsultationSession.Status.IN_PROGRESS:
            return Response(
                {"error": "session_not_in_progress", "detail": "Only in-progress sessions can be rewound."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        payload = RewindSerializer(data=request.data)
        payload.is_valid(raise_exception=True)

        session.started_at -= timedelta(minutes=payload.validated_data["minutes"])
        session.save(update_fields=["started_at", "updated_at"])
        return Response(ConsultationSessionSerializer(session).data)
