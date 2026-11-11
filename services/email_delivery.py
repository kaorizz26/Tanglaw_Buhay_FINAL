"""Send Tanglaw-Buhay account emails through a configurable SMTP server.

The module never logs and never puts message text, addresses, passwords or
tokens into an error. Callers receive only a fixed error code, so a failed send
cannot leak an activation link into logs or screens.
"""

from email.message import EmailMessage
from email.utils import parseaddr
import re
import smtplib
import ssl
from typing import NamedTuple
from urllib.parse import quote, urlsplit

from flask import current_app

from services.citizen_credentials import ACTIVATION_LIFETIME, PASSWORD_RESET_LIFETIME

SMTP_TIMEOUT_SECONDS = 10
ACTIVATION_PATH = "/portal/activate/"
PASSWORD_RESET_PATH = "/portal/reset/"
STAFF_LOGIN_PATH = "/login"
LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "::1")
ADDRESS_PATTERN = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")

# Fixed, staff-safe wording for each failure; nothing from the server is repeated.
ERROR_MESSAGES = {
    "not_configured": "Email sending is not set up.",
    "invalid_recipient": "The email address is not valid.",
    "recipient_not_allowed": "This email address is not on the allowed testing list.",
    "connection_failed": "The email server could not be reached in time.",
    "authentication_failed": "The email server refused the sender account.",
    "rejected": "The email server did not accept the message.",
    "send_failed": "The email could not be sent.",
}


class EmailDeliveryError(RuntimeError):
    """A send failed; only the fixed code and its fixed message are available."""

    def __init__(self, code):
        super().__init__(ERROR_MESSAGES[code])
        self.code = code


class EmailSettings(NamedTuple):
    """Validated SMTP connection settings."""

    host: str
    port: int
    security: str
    username: str
    password: str
    sender: str


def _setting(name):
    """Read one configuration value as trimmed text ('' when unset)."""
    value = current_app.config.get(name)
    return value.strip() if isinstance(value, str) else ""


def load_email_settings():
    """Read and check the SMTP settings; raise not_configured if anything is unusable."""
    host = _setting("SMTP_HOST")
    port_text = _setting("SMTP_PORT") or "587"
    security = (_setting("SMTP_SECURITY") or "starttls").lower()
    username = _setting("SMTP_USERNAME")
    password = current_app.config.get("SMTP_PASSWORD") or ""
    sender = _setting("SMTP_FROM")
    sender_address = parseaddr(sender)[1]
    if (
        not host or not port_text.isdecimal() or not 1 <= int(port_text) <= 65535
        or security not in ("starttls", "ssl")
        or not ADDRESS_PATTERN.fullmatch(sender_address)
        or bool(username) != bool(password)
    ):
        raise EmailDeliveryError("not_configured")
    recipient_policy()  # An unclear recipient policy also keeps email closed.
    return EmailSettings(host, int(port_text), security, username, password, sender)


def public_base_url():
    """Return the configured site address; links never use the request's Host header."""
    value = _setting("PUBLIC_BASE_URL").rstrip("/")
    parts = urlsplit(value)
    try:
        host = parts.hostname
    except ValueError:
        host = None
    secure_enough = parts.scheme == "https" or (parts.scheme == "http" and host in LOOPBACK_HOSTS)
    if not secure_enough or not host or parts.query or parts.fragment or parts.username:
        raise EmailDeliveryError("not_configured")
    return value


def build_activation_link(token):
    """Return the full activation link for a token."""
    return public_base_url() + ACTIVATION_PATH + quote(token, safe="")


def build_password_reset_link(token):
    """Return the full password-reset link for a token."""
    return public_base_url() + PASSWORD_RESET_PATH + quote(token, safe="")


def build_staff_login_link():
    """Return the configured Staff Portal sign-in address."""
    return public_base_url() + STAFF_LOGIN_PATH


def recipient_policy():
    """Return (mode, allowed addresses); raise not_configured unless the policy is clear.

    SMTP_RECIPIENT_MODE is 'allowlist' (the default) or exactly 'any'. It fails closed:
    an allowlist must be present and made of valid addresses, and 'any' may not be
    combined with a list, because the two settings would contradict each other.
    """
    mode = _setting("SMTP_RECIPIENT_MODE") or "allowlist"
    entries = [item.strip().lower() for item in _setting("SMTP_ALLOWED_RECIPIENTS").split(",")
               if item.strip()]
    if mode == "any" and not entries:
        return mode, set()
    if mode == "allowlist" and entries and all(ADDRESS_PATTERN.fullmatch(item) for item in entries):
        return mode, set(entries)
    raise EmailDeliveryError("not_configured")


def check_recipient(address):
    """Return the lowercase address if it is valid and permitted, else raise."""
    text = address.strip() if isinstance(address, str) else ""
    if (len(text) > 254 or not text.isascii() or not ADDRESS_PATTERN.fullmatch(text)
            or "," in text):
        raise EmailDeliveryError("invalid_recipient")
    mode, allowed = recipient_policy()
    if mode == "allowlist" and text.lower() not in allowed:
        raise EmailDeliveryError("recipient_not_allowed")
    return text.lower()


def send_email(to_address, subject, body):
    """Send one plain-text email; return normally only if the server accepted it.

    Acceptance by the server is not proof the message reached the inbox. Every
    failure raises EmailDeliveryError with a fixed code. The original error is
    dropped on purpose because it can contain addresses or message text.
    """
    settings = load_email_settings()
    recipient = check_recipient(to_address)
    message = EmailMessage()
    message["From"] = settings.sender
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(body)
    try:
        _deliver(settings, message)
    except smtplib.SMTPAuthenticationError:
        raise EmailDeliveryError("authentication_failed") from None
    except (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused, smtplib.SMTPDataError):
        raise EmailDeliveryError("rejected") from None
    except (smtplib.SMTPConnectError, smtplib.SMTPServerDisconnected):
        raise EmailDeliveryError("connection_failed") from None
    except smtplib.SMTPException:
        # Must come before OSError: smtplib's exceptions are OSError subclasses.
        raise EmailDeliveryError("send_failed") from None
    except OSError:
        # Timeouts, refused connections, DNS and TLS failures.
        raise EmailDeliveryError("connection_failed") from None


def _deliver(settings, message):
    """Open one encrypted connection, sign in if needed and hand over the message."""
    context = ssl.create_default_context()
    if settings.security == "ssl":
        server = smtplib.SMTP_SSL(
            settings.host, settings.port, timeout=SMTP_TIMEOUT_SECONDS, context=context,
        )
    else:
        server = smtplib.SMTP(settings.host, settings.port, timeout=SMTP_TIMEOUT_SECONDS)
    with server:
        if settings.security == "starttls":
            server.ehlo()
            server.starttls(context=context)
        server.ehlo()
        if settings.username:
            server.login(settings.username, settings.password)
        server.send_message(message)


def _lifetime_text(lifetime):
    """Write a lifetime as '48 hours' or '1 hour'."""
    hours = int(lifetime.total_seconds() // 3600)
    return f"{hours} hour" + ("" if hours == 1 else "s")


def send_activation_email(to_address, token):
    """Email the activation link. The six-digit code is never put in the email."""
    body = (
        "Hello,\n\n"
        "You have been invited to create a Tanglaw-Buhay Citizen Portal account.\n\n"
        "To activate your account, open this link:\n"
        f"{build_activation_link(token)}\n\n"
        "You will be asked for the 6-digit activation code that LGU staff gave you "
        f"separately. The link and the code stop working after {_lifetime_text(ACTIVATION_LIFETIME)}.\n\n"
        "If you did not expect this message, you can ignore it.\n\n"
        "Tanglaw-Buhay\n"
    )
    send_email(to_address, "Activate your Tanglaw-Buhay account", body)


def send_password_reset_email(to_address, token):
    """Email a one-time password-reset link."""
    body = (
        "Hello,\n\n"
        "We received a request to reset the password of your Tanglaw-Buhay "
        "Citizen Portal account.\n\n"
        "To choose a new password, open this link:\n"
        f"{build_password_reset_link(token)}\n\n"
        f"The link works once and stops working after {_lifetime_text(PASSWORD_RESET_LIFETIME)}. "
        "If you did not ask for this, ignore this message and your password will stay the same.\n\n"
        "Tanglaw-Buhay\n"
    )
    send_email(to_address, "Reset your Tanglaw-Buhay password", body)


def send_email_changed_notice(to_address):
    """Tell the previous address its account email was changed.

    The notice has no link, code or password, and does not name the new address.
    """
    body = (
        "Hello,\n\n"
        "The email address of your Tanglaw-Buhay Citizen Portal account was changed "
        "by LGU staff, and you have been signed out. This address will no longer "
        "receive messages for the account.\n\n"
        "If you asked for this change, no action is needed.\n"
        "If you did not, please contact the LGU office as soon as possible.\n\n"
        "Tanglaw-Buhay\n"
    )
    send_email(to_address, "Your Tanglaw-Buhay account email was changed", body)


def send_password_changed_email(to_address):
    """Tell the citizen their password was just changed. The email has no link."""
    body = (
        "Hello,\n\n"
        "The password of your Tanglaw-Buhay Citizen Portal account was just changed, "
        "and you have been signed out everywhere.\n\n"
        "If you made this change, no action is needed.\n"
        "If you did not, please contact the LGU office as soon as possible.\n\n"
        "Tanglaw-Buhay\n"
    )
    send_email(to_address, "Your Tanglaw-Buhay password was changed", body)


def send_staff_temporary_password_email(to_address, temporary_password, *, account_action):
    """Send a newly issued Staff Portal password to an in-person verified address."""
    action_text = {
        "created": "created your Tanglaw-Buhay Staff Portal account",
        "reset": "reset your Tanglaw-Buhay Staff Portal account password",
        "reactivated": "reactivated your Tanglaw-Buhay Staff Portal account",
    }[account_action]
    body = (
        "Hello,\n\n"
        f"An LGU System Administrator {action_text} while assisting you in person.\n\n"
        f"Temporary password: {temporary_password}\n\n"
        "Sign in with your existing username at:\n"
        f"{build_staff_login_link()}\n\n"
        "You will be required to replace this temporary password immediately after signing in. "
        "Do not forward or share this message.\n\n"
        "If you did not request this account action, contact your System Administrator and do "
        "not use the temporary password.\n\n"
        "Tanglaw-Buhay\n"
    )
    send_email(to_address, "Your Tanglaw-Buhay Staff Portal temporary password", body)
