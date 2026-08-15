import hashlib
import hmac
import time

import pytest

from app.config import settings
from app.slack.verify import SlackVerificationError, verify_slack_signature


def _sign(secret: str, timestamp: str, body: bytes) -> str:
    basestring = f"v0:{timestamp}:{body.decode()}"
    return "v0=" + hmac.new(secret.encode(), basestring.encode(), hashlib.sha256).hexdigest()


def test_valid_signature_passes(monkeypatch):
    monkeypatch.setattr(settings, "slack_signing_secret", "test-secret")
    body = b"payload=hello"
    timestamp = str(int(time.time()))
    sig = _sign("test-secret", timestamp, body)
    verify_slack_signature(body=body, timestamp=timestamp, signature=sig)  # must not raise


def test_wrong_secret_rejected(monkeypatch):
    monkeypatch.setattr(settings, "slack_signing_secret", "test-secret")
    body = b"payload=hello"
    timestamp = str(int(time.time()))
    sig = _sign("wrong-secret", timestamp, body)
    with pytest.raises(SlackVerificationError):
        verify_slack_signature(body=body, timestamp=timestamp, signature=sig)


def test_stale_timestamp_rejected(monkeypatch):
    monkeypatch.setattr(settings, "slack_signing_secret", "test-secret")
    body = b"payload=hello"
    timestamp = str(int(time.time()) - 3600)  # 1 hour old
    sig = _sign("test-secret", timestamp, body)
    with pytest.raises(SlackVerificationError):
        verify_slack_signature(body=body, timestamp=timestamp, signature=sig)


def test_no_secret_configured_rejects(monkeypatch):
    monkeypatch.setattr(settings, "slack_signing_secret", "")
    with pytest.raises(SlackVerificationError):
        verify_slack_signature(body=b"x", timestamp=str(int(time.time())), signature="v0=x")
