from rest_framework import serializers

from .models import ConsultationSession


class ConsultationSessionSerializer(serializers.ModelSerializer):
    class Meta:
        model = ConsultationSession
        fields = [
            "id",
            "user",
            "provider",
            "status",
            "per_minute_cost",
            "started_at",
            "ended_at",
            "ended_by",
            "end_reason",
            "billed_minutes",
            "billed_amount",
            "duration_seconds",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class AssignSerializer(serializers.Serializer):
    provider_id = serializers.IntegerField()
