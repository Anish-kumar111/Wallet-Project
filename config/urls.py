from django.conf import settings
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/auth/", include("accounts.urls")),
    path("api/wallet/", include("wallet.urls")),
    path("api/sessions/", include("consultations.urls")),
]

if settings.DEBUG:
    from consultations.dev_views import RewindSessionClockView

    urlpatterns += [
        path("api/dev/sessions/<int:pk>/rewind/", RewindSessionClockView.as_view(), name="dev-session-rewind"),
    ]
