from django.contrib import admin

from .models import ConsultationSession


@admin.register(ConsultationSession)
class ConsultationSessionAdmin(admin.ModelAdmin):
    list_display = ["id", "user", "provider", "status", "per_minute_cost", "started_at", "ended_at", "ended_by"]
    list_filter = ["status", "ended_by"]
