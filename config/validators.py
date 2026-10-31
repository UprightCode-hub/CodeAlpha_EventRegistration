from django.core.validators import RegexValidator, URLValidator

phone_validator = RegexValidator(
    regex=r"^\+[1-9]\d{7,14}$",
    message="Enter a phone number with + followed by 8 to 15 digits, starting with a nonzero digit.",
)
http_https_link_validator = URLValidator(
    schemes=("http", "https"),
    message="Enter a valid HTTP or HTTPS URL.",
)
