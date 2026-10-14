from django.contrib import admin

from .models import RechargeTransaction, Wallet, WalletTransaction


@admin.register(Wallet)
class WalletAdmin(admin.ModelAdmin):
    list_display = ["user", "balance", "updated_at"]
    readonly_fields = ["balance"]


@admin.register(WalletTransaction)
class WalletTransactionAdmin(admin.ModelAdmin):
    list_display = ["wallet", "type", "amount", "balance_after", "reference", "created_at"]


@admin.register(RechargeTransaction)
class RechargeTransactionAdmin(admin.ModelAdmin):
    list_display = ["user", "amount", "reference", "status", "created_at"]
    list_filter = ["status"]
