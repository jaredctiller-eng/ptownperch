import smtplib
from unittest.mock import MagicMock, patch

import pytest

from app.config import settings
from app.email.gmail import EmailSendError, send_email


def test_raises_when_not_configured(monkeypatch):
    monkeypatch.setattr(settings, "gmail_address", "")
    monkeypatch.setattr(settings, "gmail_app_password", "")
    with pytest.raises(EmailSendError):
        send_email(to="guest@example.com", subject="Hi", body="Body")


def test_sends_via_smtp_with_starttls_and_login(monkeypatch):
    monkeypatch.setattr(settings, "gmail_address", "host@gmail.com")
    monkeypatch.setattr(settings, "gmail_app_password", "app-password")

    mock_smtp = MagicMock()
    mock_smtp.__enter__.return_value = mock_smtp
    with patch("app.email.gmail.smtplib.SMTP", return_value=mock_smtp) as smtp_cls:
        send_email(to="guest@example.com", subject="Hi", body="Body text")

    smtp_cls.assert_called_once_with("smtp.gmail.com", 587, timeout=15)
    mock_smtp.starttls.assert_called_once()
    mock_smtp.login.assert_called_once_with("host@gmail.com", "app-password")
    sent_message = mock_smtp.send_message.call_args[0][0]
    assert sent_message["To"] == "guest@example.com"
    assert sent_message["From"] == "host@gmail.com"
    assert sent_message["Subject"] == "Hi"


def test_smtp_failure_raises_sanitized_error(monkeypatch):
    monkeypatch.setattr(settings, "gmail_address", "host@gmail.com")
    monkeypatch.setattr(settings, "gmail_app_password", "app-password")

    mock_smtp = MagicMock()
    mock_smtp.__enter__.return_value = mock_smtp
    mock_smtp.login.side_effect = smtplib.SMTPAuthenticationError(535, b"bad creds")
    with patch("app.email.gmail.smtplib.SMTP", return_value=mock_smtp), pytest.raises(EmailSendError):
        send_email(to="guest@example.com", subject="Hi", body="Body")
