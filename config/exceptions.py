from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler


class DomainError(Exception):
    """
    Base class for business-rule violations raised by the service layer.

    Services stay framework-agnostic; `api_exception_handler` maps these to
    HTTP responses of the form {"error": <code>, "detail": <message>}.
    """

    code = "domain_error"
    status_code = status.HTTP_400_BAD_REQUEST

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def api_exception_handler(exc, context):
    if isinstance(exc, DomainError):
        return Response({"error": exc.code, "detail": exc.message}, status=exc.status_code)
    return drf_exception_handler(exc, context)
