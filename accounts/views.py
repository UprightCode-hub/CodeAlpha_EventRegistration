from rest_framework import generics, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from accounts.serializers import LoginSerializer, RegisterSerializer, UserSerializer


class RegisterView(generics.GenericAPIView):
    """Creates a public user account."""

    serializer_class = RegisterSerializer
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        return Response(UserSerializer(user).data, status=status.HTTP_201_CREATED)


class LoginView(TokenObtainPairView):
    """Exchanges a username and password for JWT tokens."""

    permission_classes = [AllowAny]
    serializer_class = LoginSerializer


class RefreshView(TokenRefreshView):
    """Exchanges a refresh token for a new access token."""

    permission_classes = [AllowAny]


class MeView(APIView):
    """Returns the signed-in user's public account fields."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(UserSerializer(request.user).data)
