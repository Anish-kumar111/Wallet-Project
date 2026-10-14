from rest_framework.routers import SimpleRouter

from .views import ConsultationSessionViewSet

router = SimpleRouter()
router.register("", ConsultationSessionViewSet, basename="session")

urlpatterns = router.urls
