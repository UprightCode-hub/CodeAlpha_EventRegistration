from drf_spectacular.utils import extend_schema, extend_schema_view, inline_serializer
from rest_framework import generics, serializers, status
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from accounts.security import blocking_seconds, clear_failures, log_blocked, record_failure
from accounts.serializers import (
    LoginCredentialsSerializer,
    LoginSerializer,
    RefreshSerializer,
    RegisterSerializer,
    UserSerializer,
)
from config.exceptions import error_response
from config.schema_helpers import BEARER_AUTH
from config.throttles import ScopedRateThrottle


class RegisterView(generics.GenericAPIView):
    """Creates a public user account."""

    serializer_class = RegisterSerializer
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "register"

    @extend_schema(
        summary="Register an account",
        request=RegisterSerializer,
        responses={201: UserSerializer},
        auth=[],
    )
    def post(self, request, *args, **kwargs):
        """Handle the submitted request."""
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        return Response(UserSerializer(user).data, status=status.HTTP_201_CREATED)


@extend_schema_view(
    post=extend_schema(
        summary="Log in and receive access tokens",
        request=LoginSerializer,
        responses={
            200: inline_serializer(
                name="TokenPairResponse",
                fields={"access": serializers.CharField(), "refresh": serializers.CharField()},
            )
        },
        auth=[],
    )
)
class LoginView(TokenObtainPairView):
    """Exchanges a username and password for JWT tokens."""

    permission_classes = (AllowAny,)  # type: ignore[assignment]  # SimpleJWT stub is too narrow.
    serializer_class = LoginSerializer
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"

    def post(self, request, *args, **kwargs):
        """Validate credentials, enforce lockout, and issue JWT tokens."""
        body = LoginCredentialsSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        username = body.validated_data["username"]
        password = body.validated_data["password"]
        address = request.META.get("REMOTE_ADDR", "")

        retry_after = blocking_seconds(username, address)
        if retry_after is not None:
            log_blocked(username, address)
            return error_response("too_many_attempts", headers={"Retry-After": str(retry_after)})

        if len(username) > 150 or len(password) > 128:
            record_failure(username, address)
            return error_response("not_authenticated")

        serializer = self.get_serializer(data={"username": username, "password": password})
        try:
            serializer.is_valid(raise_exception=True)
        except (AuthenticationFailed, serializers.ValidationError):
            record_failure(username, address)
            return error_response("not_authenticated")

        clear_failures(username, address)
        return Response(serializer.validated_data, status=status.HTTP_200_OK)


@extend_schema_view(
    post=extend_schema(
        summary="Refresh an access token",
        request=RefreshSerializer,
        responses={
            200: inline_serializer(
                name="AccessTokenResponse", fields={"access": serializers.CharField()}
            )
        },
        auth=[],
    )
)
class RefreshView(TokenRefreshView):
    """Exchanges a refresh token for a new access token."""

    permission_classes = (AllowAny,)  # type: ignore[assignment]  # SimpleJWT stub is too narrow.


class MeView(APIView):
    """Returns the signed-in user's public account fields."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Get the signed-in account",
        responses={200: UserSerializer},
        auth=BEARER_AUTH,
    )
    def get(self, request):
        """Return the requested data."""
        return Response(UserSerializer(request.user).data)
