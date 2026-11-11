"""Check the Tanglaw-Buhay email settings. Nothing is sent unless explicitly confirmed.

Without options it only validates the settings in .env and prints a summary
(no passwords, usernames or full addresses). To send one plain test message:

    python scripts/check_email_settings.py --send-to you@example.com \
        --confirm SEND-TEST-EMAIL

The message contains no account link. In 'allowlist' mode only addresses on
SMTP_ALLOWED_RECIPIENTS can receive it; in 'any' mode any valid address can.
"""

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from flask import Flask

from config import Config
from services import email_delivery

CONFIRMATION = "SEND-TEST-EMAIL"
TEST_SUBJECT = "Tanglaw-Buhay email settings test"
TEST_BODY = (
    "This is a test message from the Tanglaw-Buhay email settings check.\n"
    "No action is needed.\n"
)


def build_app():
    """Create a minimal Flask app that only carries the configuration."""
    app = Flask(__name__)
    app.config.from_object(Config)
    return app


def summarize_settings():
    """Validate the settings and describe them without revealing any secret."""
    settings = email_delivery.load_email_settings()
    email_delivery.public_base_url()
    mode, allowed = email_delivery.recipient_policy()
    return {
        "smtp_host": settings.host, "smtp_port": settings.port,
        "security": settings.security, "signs_in": bool(settings.username),
        "sender_domain": settings.sender.rsplit("@", 1)[-1].rstrip("> "),
        "recipient_mode": mode,
        "allowed_recipient_count": len(allowed),
        "public_base_url_valid": True,
    }


def parse_arguments(argv):
    """Read the command-line options; the default is a settings check only."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--send-to", help="send one test message to this address")
    parser.add_argument("--confirm", default="", help=f"must be {CONFIRMATION} to send")
    return parser.parse_args(argv)


def main(argv=None):
    """Run the check, or the confirmed test send, and print a JSON result."""
    arguments = parse_arguments(sys.argv[1:] if argv is None else argv)
    if arguments.send_to and arguments.confirm != CONFIRMATION:
        print(json.dumps({"ok": False, "error": "confirmation_required"}))
        return 1
    result = {}
    try:
        with build_app().app_context():
            result["settings"] = summarize_settings()
            if arguments.send_to:
                email_delivery.send_email(arguments.send_to, TEST_SUBJECT, TEST_BODY)
                result["sent"] = True
                result["recipient_domain"] = arguments.send_to.rsplit("@", 1)[-1].lower()
            else:
                result["sent"] = False
    except email_delivery.EmailDeliveryError as error:
        # Only the fixed code is shown; server messages and addresses are never printed.
        print(json.dumps({"ok": False, "error": error.code}))
        return 1
    print(json.dumps({"ok": True, **result}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
