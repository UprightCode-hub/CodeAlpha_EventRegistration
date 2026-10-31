from rest_framework import generics, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from drf_spectacular.utils import extend_schema, extend_schema_view, inline_serializer
from rest_framework import serializers
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from accounts.serializers import (
    LoginSerializer,
    RefreshSerializer,
    RegisterSerializer,
    UserSerializer,
)


class RegisterView(generics.GenericAPIView):
    """Creates a public user account."""

    serializer_class = RegisterSerializer
    permission_classes = [AllowAny]

    @extend_schema(
        summary="Register an account",
        request=RegisterSerializer,
        responses={201: UserSerializer},
        auth=[],
    )
    def post(self, request, *args, **kwargs):
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

    permission_classes = [AllowAny]
    serializer_class = LoginSerializer


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

    permission_classes = [AllowAny]


class MeView(APIView):
    """Returns the signed-in user's public account fields."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Get the signed-in account",
        responses={200: UserSerializer},
        auth=[{"BearerAuth": []}],
    )
    def get(self, request):
        return Response(UserSerializer(request.user).data)
