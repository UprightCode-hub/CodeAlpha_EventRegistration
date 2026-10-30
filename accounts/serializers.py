from django.contrib.auth import get_user_model, password_validation
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

User = get_user_model()


class RegisterSerializer(serializers.Serializer):
    """Validates registration details and creates a public account."""

    username = serializers.CharField(max_length=150)
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, trim_whitespace=False)
    role = serializers.ChoiceField(choices=User.Role.choices)

    def validate_username(self, value):
        if User.objects.filter(username__iexact=value).exists():
            raise serializers.ValidationError("A user with that username already exists.")
        return value

    def validate_email(self, value):
        value = value.lower()
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("A user with that email already exists.")
        return value

    def validate(self, attrs):
        try:
            password_validation.validate_password(
                attrs["password"],
                user=User(username=attrs["username"], email=attrs["email"]),
            )
        except DjangoValidationError as exc:
            raise serializers.ValidationError({"password": list(exc.messages)}) from exc
        return attrs

    def create(self, validated_data):
        return User.objects.create_user(**validated_data)


class UserSerializer(serializers.ModelSerializer):
    """Returns the public account fields."""

    class Meta:
        """Defines the public account fields returned by the serializer."""

        model = User
        fields = ("id", "username", "email", "role")
        read_only_fields = fields


class LoginSerializer(TokenObtainPairSerializer):
    """Authenticates credentials and returns access and refresh tokens."""


class RefreshSerializer(serializers.Serializer):
    """Validates a refresh token and returns a new access token."""

    refresh = serializers.CharField()
