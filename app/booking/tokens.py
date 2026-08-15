import secrets


def generate_booking_token() -> str:
    return secrets.token_urlsafe(24)
