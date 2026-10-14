from django.db.models import Q
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from accounts.permissions import IsUserRole

from . import billing, services
from .models import ConsultationSession
from .serializers import AssignSerializer, ConsultationSessionSerializer


class ConsultationSessionViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin, viewsets.GenericViewSet
):
    """
    Sessions visible to the caller (as user or as provider), plus one POST
    endpoint per state-machine action. Business rules live in `services`.
    """

    serializer_class = ConsultationSessionSerializer

    def get_queryset(self):
        me = self.request.user
        return ConsultationSession.objects.filter(Q(user=me) | Q(provider=me))

    def get_permissions(self):
        if self.action == "create":
            return [IsUserRole()]
        return super().get_permissions()

    def create(self, request, *args, **kwargs):
        session = services.create_session(request.user)
        return Response(self.get_serializer(session).data, status=status.HTTP_201_CREATED)

    def _respond(self, session):
        return Response(self.get_serializer(session).data)

    @action(detail=True, methods=["post"])
    def assign(self, request, pk=None):
        session = self.get_object()
        payload = AssignSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        return self._respond(services.assign_session(session.pk, request.user, payload.validated_data["provider_id"]))

    @action(detail=True, methods=["post"])
    def accept(self, request, pk=None):
        return self._respond(services.accept_session(self.get_object().pk, request.user))

    @action(detail=True, methods=["post"])
    def start(self, request, pk=None):
        return self._respond(services.start_session(self.get_object().pk, request.user))

    @action(detail=True, methods=["post"])
    def end(self, request, pk=None):
        return self._respond(services.end_session(self.get_object().pk, request.user))

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        return self._respond(services.cancel_session(self.get_object().pk, request.user))

    @action(detail=True, methods=["post"])
    def bill(self, request, pk=None):
        """Charge all full minutes elapsed so far. Safe to call any number of times."""
        return self._respond(billing.bill_session(self.get_object().pk))
