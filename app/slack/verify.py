import hashlib
import hmac
import time

from app.config import settings

REPLAY_WINDOW_SECONDS = 60 * 5


class SlackVerificationError(Exception):
    pass


def verify_slack_signature(*, body: bytes, timestamp: str, signature: str) -> None:
    if not settings.slack_signing_secret:
        raise SlackVerificationError("signing secret not configured")
    try:
        ts = int(timestamp)
    except (TypeError, ValueError):
        raise SlackVerificationError("invalid timestamp") from None
    if abs(time.time() - ts) > REPLAY_WINDOW_SECONDS:
        raise SlackVerificationError("stale request")

    basestring = f"v0:{timestamp}:{body.decode()}"
    computed = "v0=" + hmac.new(
        settings.slack_signing_secret.encode(), basestring.encode(), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(computed, signature):
        raise SlackVerificationError("signature mismatch")
