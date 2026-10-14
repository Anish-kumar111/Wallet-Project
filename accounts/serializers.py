from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers

User = get_user_model()


def _validate_rate_for_role(role, rate):
    if role == User.Role.PROVIDER and rate is None:
        raise serializers.ValidationError({"per_minute_rate": "Providers must set a per-minute rate."})
    if role == User.Role.USER and rate is not None:
        raise serializers.ValidationError({"per_minute_rate": "Only providers can have a per-minute rate."})


class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, validators=[validate_password])
    per_minute_rate = serializers.DecimalField(
        max_digits=10, decimal_places=2, min_value=Decimal("0.01"), required=False, allow_null=True
    )

    class Meta:
        model = User
        fields = ["id", "username", "email", "password", "role", "per_minute_rate"]

    def validate(self, attrs):
        _validate_rate_for_role(attrs.get("role", User.Role.USER), attrs.get("per_minute_rate"))
        return attrs

    def create(self, validated_data):
        return User.objects.create_user(**validated_data)


class UserSerializer(serializers.ModelSerializer):
    per_minute_rate = serializers.DecimalField(
        max_digits=10, decimal_places=2, min_value=Decimal("0.01"), required=False, allow_null=True
    )

    class Meta:
        model = User
        fields = ["id", "username", "email", "role", "per_minute_rate"]
        read_only_fields = ["id", "username", "role"]

    def validate(self, attrs):
        if "per_minute_rate" in attrs:
            _validate_rate_for_role(self.instance.role, attrs["per_minute_rate"])
        return attrs
